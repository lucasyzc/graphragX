from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    ChunkACL,
    Document,
    DocumentChunk,
    IngestionCheckpoint,
    KnowledgeJob,
    KnowledgeSource,
)
from app.db.session import SessionLocal
from app.schemas.knowledge import KnowledgeSourceCreate, KnowledgeSourceUpdate
from app.services.audit_service import write_audit
from app.services.embedding_service import EmbeddingService
from app.vector.repository import VectorRepository
from app.vector.types import VectorChunk

_JOB_LOCKS: dict[str, threading.Lock] = {}
_JOB_LOCKS_GUARD = threading.Lock()


@dataclass
class ParsedDocument:
    source_uri: str
    title: str
    content: str
    etag: str | None
    last_modified: str | None
    mtime: str | None

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


def create_source(db: Session, payload: KnowledgeSourceCreate) -> KnowledgeSource:
    row = KnowledgeSource(
        project_id=payload.project_id,
        name=payload.name.strip(),
        source_type=payload.source_type,
        source_uri=payload.source_uri.strip(),
        config_json=json.dumps({"tags": payload.tags}, ensure_ascii=False),
        enabled=payload.enabled,
        updated_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_sources(db: Session, project_id: str | None = None) -> list[KnowledgeSource]:
    query = db.query(KnowledgeSource)
    if project_id:
        query = query.filter(KnowledgeSource.project_id == project_id)
    return query.order_by(KnowledgeSource.created_at.desc()).all()


def get_source(db: Session, source_id: str) -> KnowledgeSource | None:
    return db.query(KnowledgeSource).filter(KnowledgeSource.id == source_id).first()


def update_source(db: Session, source: KnowledgeSource, payload: KnowledgeSourceUpdate) -> KnowledgeSource:
    if payload.name is not None:
        source.name = payload.name.strip()
    if payload.source_uri is not None:
        source.source_uri = payload.source_uri.strip()
    if payload.enabled is not None:
        source.enabled = bool(payload.enabled)
    if payload.tags is not None:
        cfg = _source_config(source)
        cfg["tags"] = payload.tags
        source.config_json = json.dumps(cfg, ensure_ascii=False)
    source.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(source)
    return source


def create_knowledge_job(db: Session, project_id: str, source_id: str, mode: str) -> KnowledgeJob:
    row = KnowledgeJob(project_id=project_id, source_id=source_id, mode=mode, status="queued")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_knowledge_jobs(
    db: Session,
    project_id: str | None = None,
    source_id: str | None = None,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[KnowledgeJob], int]:
    query = db.query(KnowledgeJob)
    if project_id:
        query = query.filter(KnowledgeJob.project_id == project_id)
    if source_id:
        query = query.filter(KnowledgeJob.source_id == source_id)
    if status:
        query = query.filter(KnowledgeJob.status == status)
    total = query.count()
    items = query.order_by(KnowledgeJob.created_at.desc()).offset(offset).limit(limit).all()
    return items, total


def run_knowledge_sync_job(job_id: str, source_id: str, mode: str, actor: str) -> None:
    lock = _source_lock(source_id)
    with lock:
        _run_knowledge_sync_job_locked(job_id=job_id, source_id=source_id, mode=mode, actor=actor)


def _run_knowledge_sync_job_locked(job_id: str, source_id: str, mode: str, actor: str) -> None:
    settings = get_settings()
    db: Session = SessionLocal()
    vector_repo = VectorRepository()
    embedding = EmbeddingService()
    try:
        job = db.query(KnowledgeJob).filter(KnowledgeJob.id == job_id).first()
        source = db.query(KnowledgeSource).filter(KnowledgeSource.id == source_id).first()
        if not job or not source:
            return
        job.status = "running"
        job.started_at = datetime.utcnow()
        db.commit()

        if mode == "full":
            stale_source_uris = {
                row[0]
                for row in db.query(Document.source_uri).filter(Document.source_id == source.id).all()
                if row[0]
            }
            if stale_source_uris:
                vector_repo.delete_by_files(
                    project_id=source.project_id,
                    file_paths=stale_source_uris,
                    source_types={"doc"},
                )
            _delete_source_documents(db=db, source_id=source.id, project_id=source.project_id)
            db.query(IngestionCheckpoint).filter(IngestionCheckpoint.source_id == source.id).delete()
            db.commit()

        parsed_docs = _collect_documents(source=source)
        stats = {"scanned": 0, "indexed": 0, "skipped": 0}
        all_vector_chunks: list[VectorChunk] = []
        all_embeddings_input: list[str] = []
        for item in parsed_docs:
            stats["scanned"] += 1
            checkpoint = (
                db.query(IngestionCheckpoint)
                .filter(
                    IngestionCheckpoint.source_id == source.id,
                    IngestionCheckpoint.source_uri == item.source_uri,
                )
                .first()
            )
            unchanged = (
                mode != "full"
                and checkpoint is not None
                and checkpoint.content_hash == item.content_hash
                and (checkpoint.etag or "") == (item.etag or "")
                and (checkpoint.mtime or "") == (item.mtime or "")
            )
            if unchanged:
                stats["skipped"] += 1
                continue

            doc = (
                db.query(Document)
                .filter(Document.source_id == source.id, Document.source_uri == item.source_uri)
                .first()
            )
            if not doc:
                doc = Document(
                    project_id=source.project_id,
                    source_id=source.id,
                    title=item.title,
                    source_uri=item.source_uri,
                    content_hash=item.content_hash,
                    etag=item.etag,
                    last_modified=item.last_modified,
                    updated_at=datetime.utcnow(),
                )
                db.add(doc)
                db.commit()
                db.refresh(doc)
            else:
                doc.title = item.title
                doc.content_hash = item.content_hash
                doc.etag = item.etag
                doc.last_modified = item.last_modified
                doc.updated_at = datetime.utcnow()
                db.commit()

            existing_chunk_ids = [row[0] for row in db.query(DocumentChunk.id).filter(DocumentChunk.document_id == doc.id).all()]
            if existing_chunk_ids:
                if mode != "full":
                    vector_repo.delete_by_files(
                        project_id=source.project_id,
                        file_paths={item.source_uri},
                        source_types={"doc"},
                    )
                db.query(ChunkACL).filter(
                    ChunkACL.chunk_source == "document_chunk",
                    ChunkACL.project_id == source.project_id,
                    ChunkACL.chunk_id.in_(existing_chunk_ids),
                ).delete(synchronize_session=False)
            db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).delete(synchronize_session=False)
            db.commit()

            tags = _source_tags(source)
            chunks = _split_text(
                item.content,
                max_chars=settings.knowledge_chunk_chars,
                overlap=settings.knowledge_chunk_overlap,
            )
            for chunk_index, chunk_text in enumerate(chunks):
                if not chunk_text.strip():
                    continue
                start_offset = item.content.find(chunk_text[:20]) if len(chunk_text) >= 20 else 0
                end_offset = max(start_offset, 0) + len(chunk_text)
                row = DocumentChunk(
                    project_id=source.project_id,
                    document_id=doc.id,
                    source_id=source.id,
                    title=item.title,
                    source_uri=item.source_uri,
                    source_type="doc",
                    tags=",".join(tags) if tags else None,
                    chunk_index=chunk_index,
                    start_offset=max(start_offset, 0),
                    end_offset=end_offset,
                    content=chunk_text,
                    embedding_model=settings.embedding_model,
                    updated_at=datetime.utcnow(),
                )
                db.add(row)
                db.flush()
                _grant_default_acl(db=db, project_id=source.project_id, chunk_id=row.id)
                all_embeddings_input.append(chunk_text)
                all_vector_chunks.append(
                    VectorChunk(
                        chunk_id=row.id,
                        project_id=source.project_id,
                        language="text",
                        symbol_type="doc_chunk",
                        qualified_name=f"{item.title}#{chunk_index}",
                        file_path=item.source_uri,
                        start_line=1,
                        end_line=1,
                        content=chunk_text,
                        document_id=doc.id,
                        source_type="doc",
                        source_uri=item.source_uri,
                        title=item.title,
                        chunk_index=chunk_index,
                        tags=row.tags,
                    )
                )
            stats["indexed"] += 1
            _upsert_checkpoint(db=db, source_id=source.id, item=item, checkpoint=checkpoint)
            db.commit()

        if all_vector_chunks:
            vectors = embedding.embed_texts(all_embeddings_input)
            vector_repo.upsert_chunks(
                project_id=source.project_id,
                chunks=all_vector_chunks,
                embeddings=vectors,
            )

        job.status = "done"
        job.message = (
            f"knowledge sync done mode={mode} scanned={stats['scanned']} indexed={stats['indexed']} "
            f"skipped={stats['skipped']}"
        )
        job.scanned_count = stats["scanned"]
        job.indexed_count = stats["indexed"]
        job.skipped_count = stats["skipped"]
        job.finished_at = datetime.utcnow()
        db.commit()
        write_audit(
            db,
            actor=actor,
            action="knowledge.sync.done",
            project_id=source.project_id,
            detail=f"job={job.id} source={source.id} mode={mode}",
        )
    except Exception as exc:  # pragma: no cover
        db.rollback()
        row = db.query(KnowledgeJob).filter(KnowledgeJob.id == job_id).first()
        if row:
            row.status = "failed"
            row.message = str(exc)
            row.finished_at = datetime.utcnow()
            db.commit()
        if row:
            write_audit(
                db,
                actor=actor,
                action="knowledge.sync.failed",
                project_id=row.project_id,
                detail=f"job={job_id} err={exc}",
            )
    finally:
        db.close()


def _delete_source_documents(db: Session, source_id: str, project_id: str) -> None:
    document_ids = [row[0] for row in db.query(Document.id).filter(Document.source_id == source_id).all()]
    if document_ids:
        chunk_ids = [row[0] for row in db.query(DocumentChunk.id).filter(DocumentChunk.document_id.in_(document_ids)).all()]
        if chunk_ids:
            db.query(ChunkACL).filter(
                ChunkACL.project_id == project_id,
                ChunkACL.chunk_source == "document_chunk",
                ChunkACL.chunk_id.in_(chunk_ids),
            ).delete(synchronize_session=False)
        db.query(DocumentChunk).filter(DocumentChunk.document_id.in_(document_ids)).delete(synchronize_session=False)
    db.query(Document).filter(Document.source_id == source_id).delete(synchronize_session=False)
    db.commit()


def _upsert_checkpoint(
    db: Session,
    source_id: str,
    item: ParsedDocument,
    checkpoint: IngestionCheckpoint | None,
) -> None:
    if checkpoint is None:
        checkpoint = IngestionCheckpoint(source_id=source_id, source_uri=item.source_uri)
        db.add(checkpoint)
    checkpoint.etag = item.etag
    checkpoint.content_hash = item.content_hash
    checkpoint.mtime = item.mtime
    checkpoint.last_synced_at = datetime.utcnow()


def _grant_default_acl(db: Session, project_id: str, chunk_id: str) -> None:
    for role in ("viewer", "editor", "admin"):
        db.add(
            ChunkACL(
                project_id=project_id,
                chunk_source="document_chunk",
                chunk_id=chunk_id,
                principal_type="role",
                principal_id=role,
            )
        )


def _source_lock(source_id: str) -> threading.Lock:
    with _JOB_LOCKS_GUARD:
        lock = _JOB_LOCKS.get(source_id)
        if lock is None:
            lock = threading.Lock()
            _JOB_LOCKS[source_id] = lock
        return lock


def _source_config(source: KnowledgeSource) -> dict:
    raw = (source.config_json or "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


def _source_tags(source: KnowledgeSource) -> list[str]:
    cfg = _source_config(source)
    tags = cfg.get("tags")
    if not isinstance(tags, list):
        return []
    return [str(tag).strip() for tag in tags if str(tag).strip()]


def _collect_documents(source: KnowledgeSource) -> list[ParsedDocument]:
    if source.source_type == "local_dir":
        return _collect_local_dir_documents(source)
    if source.source_type == "http":
        return _collect_http_documents(source)
    raise RuntimeError(f"unsupported knowledge source type: {source.source_type}")


def _collect_local_dir_documents(source: KnowledgeSource) -> list[ParsedDocument]:
    settings = get_settings()
    root = Path(source.source_uri).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise RuntimeError(f"knowledge source directory not found: {root}")
    allowed_exts = {
        f".{item.strip().lower()}"
        for item in settings.knowledge_supported_exts.split(",")
        if item.strip()
    }
    documents: list[ParsedDocument] = []
    for file_path in sorted(root.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in allowed_exts:
            continue
        parsed_items = _parse_local_file(root=root, file_path=file_path)
        documents.extend(parsed_items)
    return documents


def _parse_local_file(root: Path, file_path: Path) -> list[ParsedDocument]:
    suffix = file_path.suffix.lower()
    raw_bytes = file_path.read_bytes()
    rel = file_path.relative_to(root).as_posix()
    stat = file_path.stat()
    mtime = str(int(stat.st_mtime))
    return _parse_blob_to_documents(
        raw_bytes=raw_bytes,
        suffix=suffix,
        default_source_uri=rel,
        default_title=file_path.name,
        mtime=mtime,
    )


def _collect_http_documents(source: KnowledgeSource) -> list[ParsedDocument]:
    url = source.source_uri.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise RuntimeError("http knowledge source requires valid http(s) URL")
    with httpx.Client(timeout=30) as client:
        resp = client.get(url)
        resp.raise_for_status()
        body = resp.content
        suffix = Path(parsed.path or "/doc.txt").suffix.lower()
        return _parse_blob_to_documents(
            raw_bytes=body,
            suffix=suffix,
            default_source_uri=url,
            default_title=Path(parsed.path or "index").name or "index",
            etag=(resp.headers.get("etag") or "").strip() or None,
            last_modified=(resp.headers.get("last-modified") or "").strip() or None,
            mtime=None,
        )


def _decode_document_bytes(raw_bytes: bytes, suffix: str) -> str:
    if suffix in {".txt", ".md"}:
        return raw_bytes.decode("utf-8", errors="ignore")
    if suffix == ".html":
        html = raw_bytes.decode("utf-8", errors="ignore")
        return re.sub(r"<[^>]+>", " ", html)
    if suffix in {".pdf", ".docx"}:
        # Lightweight fallback for MVP; may be noisy but keeps ingestion non-blocking.
        return raw_bytes.decode("utf-8", errors="ignore")
    return raw_bytes.decode("utf-8", errors="ignore")


def _parse_blob_to_documents(
    raw_bytes: bytes,
    suffix: str,
    default_source_uri: str,
    default_title: str,
    etag: str | None = None,
    last_modified: str | None = None,
    mtime: str | None = None,
) -> list[ParsedDocument]:
    normalized_suffix = (suffix or "").lower()
    if normalized_suffix in {".jsonl"}:
        records = _parse_jsonl_records(raw_bytes)
        return _records_to_documents(
            records=records,
            default_source_uri=default_source_uri,
            default_title=default_title,
            etag=etag,
            last_modified=last_modified,
            mtime=mtime,
        )
    if normalized_suffix in {".json"}:
        records = _parse_json_records(raw_bytes)
        return _records_to_documents(
            records=records,
            default_source_uri=default_source_uri,
            default_title=default_title,
            etag=etag,
            last_modified=last_modified,
            mtime=mtime,
        )

    text = _normalize_text(_decode_document_bytes(raw_bytes, normalized_suffix))
    if not text:
        return []
    return [
        ParsedDocument(
            source_uri=default_source_uri,
            title=default_title,
            content=text,
            etag=etag,
            last_modified=last_modified,
            mtime=mtime,
        )
    ]


def _parse_jsonl_records(raw_bytes: bytes) -> list[Any]:
    text = raw_bytes.decode("utf-8", errors="ignore")
    records: list[Any] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            records.append(json.loads(stripped))
        except json.JSONDecodeError:
            # Keep malformed rows searchable instead of dropping data.
            records.append({"text": stripped, "_parse_error": "invalid_jsonl_line"})
    return records


def _parse_json_records(raw_bytes: bytes) -> list[Any]:
    text = raw_bytes.decode("utf-8", errors="ignore").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return [{"text": text, "_parse_error": "invalid_json"}]
    if isinstance(payload, list):
        return payload
    return [payload]


def _records_to_documents(
    records: list[Any],
    default_source_uri: str,
    default_title: str,
    etag: str | None,
    last_modified: str | None,
    mtime: str | None,
) -> list[ParsedDocument]:
    documents: list[ParsedDocument] = []
    total = max(1, len(records))
    for idx, record in enumerate(records, start=1):
        row = _record_to_document(
            record=record,
            row_index=idx,
            total_rows=total,
            default_source_uri=default_source_uri,
            default_title=default_title,
            etag=etag,
            last_modified=last_modified,
            mtime=mtime,
        )
        if row:
            documents.append(row)
    return documents


def _record_to_document(
    record: Any,
    row_index: int,
    total_rows: int,
    default_source_uri: str,
    default_title: str,
    etag: str | None,
    last_modified: str | None,
    mtime: str | None,
) -> ParsedDocument | None:
    title = _pick_field_value(record, TITLE_FIELD_HINTS)
    if not title:
        title = default_title if total_rows == 1 else f"{default_title} [{row_index}]"
    title = str(title).strip()[:512]

    source_uri = _pick_field_value(record, URI_FIELD_HINTS)
    if not source_uri:
        rid = _pick_field_value(record, ID_FIELD_HINTS)
        if rid:
            source_uri = f"{default_source_uri}#id={str(rid).strip()}"
    if not source_uri:
        source_uri = default_source_uri if total_rows == 1 else f"{default_source_uri}#row-{row_index}"
    source_uri = str(source_uri).strip()[:1024]

    content = _build_record_content(record)
    content = _normalize_text(content)
    if not content:
        return None
    return ParsedDocument(
        source_uri=source_uri,
        title=title,
        content=content,
        etag=etag,
        last_modified=last_modified,
        mtime=mtime,
    )


TITLE_FIELD_HINTS = (
    "title",
    "name",
    "headline",
    "subject",
    "question",
)
URI_FIELD_HINTS = (
    "url",
    "uri",
    "source_url",
    "link",
    "path",
    "slug",
)
ID_FIELD_HINTS = ("id", "uuid", "key", "doc_id", "record_id")
PRIMARY_TEXT_HINTS = (
    "contents",
    "content",
    "text",
    "body",
    "description",
    "summary",
    "answer",
    "article",
    "html_content",
    "markdown",
    "md",
)


def _pick_field_value(record: Any, hints: tuple[str, ...]) -> str | None:
    if isinstance(record, dict):
        lowered = {str(k).lower(): k for k in record.keys()}
        for hint in hints:
            key = lowered.get(hint)
            if key is None:
                continue
            value = record.get(key)
            text = _value_to_text(value, key_hint=str(key).lower())
            if text:
                return text
    # Fallback: recursive search by suffix match.
    for path, value in _flatten_scalars(record):
        for hint in hints:
            if path.endswith(hint):
                text = _value_to_text(value, key_hint=path)
                if text:
                    return text
    return None


def _build_record_content(record: Any) -> str:
    parts: list[str] = []
    meta_parts: list[str] = []

    title = _pick_field_value(record, TITLE_FIELD_HINTS)
    if title:
        meta_parts.append(f"title={title}")
    uri = _pick_field_value(record, URI_FIELD_HINTS)
    if uri:
        meta_parts.append(f"url={uri}")
    rid = _pick_field_value(record, ID_FIELD_HINTS)
    if rid:
        meta_parts.append(f"id={rid}")

    primary_body = _extract_primary_text(record)
    if primary_body:
        parts.append(primary_body)

    flat_lines: list[str] = []
    for key, value in _flatten_scalars(record):
        if not value:
            continue
        if key.endswith("_parse_error"):
            continue
        if key in PRIMARY_TEXT_HINTS:
            continue
        flat_lines.append(f"{key}: {value}")
    if flat_lines:
        parts.append("\n".join(flat_lines[:120]))

    if meta_parts:
        return "\n".join(meta_parts) + "\n\n" + "\n\n".join(part for part in parts if part)
    return "\n\n".join(part for part in parts if part)


def _extract_primary_text(record: Any) -> str:
    segments: list[str] = []
    if isinstance(record, dict):
        lowered = {str(k).lower(): k for k in record.keys()}
        for hint in PRIMARY_TEXT_HINTS:
            key = lowered.get(hint)
            if key is None:
                continue
            raw = record.get(key)
            text = _value_to_text(raw, key_hint=str(key).lower())
            if text:
                segments.append(text)
        if segments:
            return "\n\n".join(segments)
    text = _value_to_text(record, key_hint="")
    return text


def _value_to_text(value: Any, key_hint: str) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        raw = value
    elif isinstance(value, (int, float, bool)):
        raw = str(value)
    elif isinstance(value, list):
        rows = [_value_to_text(item, key_hint=key_hint) for item in value]
        raw = "\n".join(item for item in rows if item)
    elif isinstance(value, dict):
        rows = [f"{k}: {_value_to_text(v, key_hint=str(k).lower())}" for k, v in value.items()]
        raw = "\n".join(item for item in rows if item and not item.endswith(": "))
    else:
        raw = str(value)

    if "html" in (key_hint or ""):
        raw = re.sub(r"<[^>]+>", " ", raw)
    return _normalize_text(raw)


def _flatten_scalars(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, sub in value.items():
            next_prefix = f"{prefix}.{key}" if prefix else str(key)
            out.extend(_flatten_scalars(sub, next_prefix.lower()))
        return out
    if isinstance(value, list):
        for idx, sub in enumerate(value):
            next_prefix = f"{prefix}[{idx}]"
            out.extend(_flatten_scalars(sub, next_prefix.lower()))
        return out
    if value is None:
        return out
    text = _value_to_text(value, key_hint=prefix.lower())
    if text:
        out.append((prefix.lower(), text))
    return out


def _normalize_text(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.replace("\r\n", "\n").splitlines()]
    out = "\n".join(line for line in lines if line)
    return out.strip()


def _split_text(text: str, max_chars: int, overlap: int) -> list[str]:
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    size = max(200, max_chars)
    gap = max(0, min(overlap, size // 2))
    while start < len(text):
        end = min(len(text), start + size)
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(0, end - gap)
    return chunks


def query_document_chunks_for_keyword(
    db: Session,
    project_id: str,
    actor_role: str,
    tokens: Iterable[str],
    limit: int,
    tags: set[str] | None = None,
    source_uri: str | None = None,
) -> list[DocumentChunk]:
    query = db.query(DocumentChunk).filter(DocumentChunk.project_id == project_id)
    if source_uri:
        query = query.filter(DocumentChunk.source_uri.contains(source_uri))
    if tags:
        for tag in sorted(tags):
            query = query.filter(DocumentChunk.tags.contains(tag))

    allowed_ids = {
        row[0]
        for row in db.query(ChunkACL.chunk_id)
        .filter(
            ChunkACL.project_id == project_id,
            ChunkACL.chunk_source == "document_chunk",
            ChunkACL.principal_type == "role",
            ChunkACL.principal_id == actor_role,
        )
        .all()
    }
    if not allowed_ids:
        return []

    base_rows = query.filter(DocumentChunk.id.in_(allowed_ids)).limit(max(limit * 4, 40)).all()
    token_set = {token.lower() for token in tokens if token}
    if not token_set:
        return base_rows[:limit]
    scored: list[tuple[float, DocumentChunk]] = []
    for row in base_rows:
        content_tokens = _tokenize(row.content)
        overlap = len(token_set.intersection(content_tokens))
        score = overlap / max(1, len(token_set))
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [row for _, row in scored[:limit]]


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", (text or "").lower()))
