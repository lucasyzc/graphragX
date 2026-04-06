from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import CodeChunk, Project, Symbol, SyncJob
from app.db.session import SessionLocal
from app.graph.repository import GraphRepository
from app.services.audit_service import write_audit
from app.services.chunking_service import ChunkingService
from app.services.embedding_service import EmbeddingService
from app.services.indexing_service import IndexingResult, IndexingService
from app.services.job_service import get_last_success_job_for_project, mark_done, mark_failed, mark_running
from app.services.scm_service import (
    SCMError,
    changed_files_between,
    checkout_ref,
    commit_exists,
    ensure_repo_checkout,
    get_head_sha,
    is_git_repo,
)
from app.vector.repository import VectorRepository
from app.vector.types import VectorChunk

_SYNC_LOCKS: dict[str, threading.Lock] = {}
_SYNC_LOCKS_GUARD = threading.Lock()


@dataclass
class SyncPlan:
    replace: bool
    effective_mode: str
    changed_files: set[str]
    deleted_files: set[str]
    stale_files: set[str]
    include_files: set[str] | None
    base_sha: str | None
    head_sha: str | None
    reason: str


def run_sync_job(
    job_id: str,
    project_id: str,
    mode: str,
    actor: str,
    base_sha: str | None = None,
    head_sha: str | None = None,
    since_sha: str | None = None,
) -> None:
    lock = _project_lock(project_id)
    with lock:
        _run_sync_job_locked(
            job_id=job_id,
            project_id=project_id,
            mode=mode,
            actor=actor,
            base_sha=base_sha,
            head_sha=head_sha,
            since_sha=since_sha,
        )


def _run_sync_job_locked(
    job_id: str,
    project_id: str,
    mode: str,
    actor: str,
    base_sha: str | None,
    head_sha: str | None,
    since_sha: str | None,
) -> None:
    settings = get_settings()
    graph_repo = GraphRepository()
    vector_repo = VectorRepository()
    indexing = IndexingService()
    chunking = ChunkingService()
    embedding = EmbeddingService()

    db: Session = SessionLocal()
    try:
        mark_running(db, job_id)
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise RuntimeError(f"Project not found: {project_id}")

        if settings.sync_mock_mode:
            _mock_sync(db, project_id)
            mock_symbols = db.query(Symbol).filter(Symbol.project_id == project_id).all()
            mock_chunks = db.query(CodeChunk).filter(CodeChunk.project_id == project_id).all()
            vectors = embedding.embed_texts([row.content for row in mock_chunks])
            vector_repo.delete_project(project_id)
            vector_repo.upsert_chunks(
                project_id=project_id,
                chunks=[
                    VectorChunk(
                        chunk_id=row.id,
                        project_id=row.project_id,
                        language=row.language,
                        symbol_type=row.symbol_type,
                        qualified_name=row.qualified_name,
                        file_path=row.file_path,
                        start_line=row.start_line,
                        end_line=row.end_line,
                        content=row.content,
                        symbol_id=row.symbol_id,
                        source_type=row.source_type or "code",
                        source_uri=row.source_uri,
                        title=row.title,
                        tags=row.tags,
                    )
                    for row in mock_chunks
                ],
                embeddings=vectors,
            )
            graph_repo.upsert_symbols(
                project_id=project_id,
                symbols=mock_symbols,
                edges=[],
                replace=True,
            )
            mark_done(db, job_id, message="Sync completed in mock mode")
            write_audit(db, actor=actor, action="sync.done", project_id=project_id, detail=f"job={job_id} mode=mock")
            return

        repo_dir = ensure_repo_checkout(project)
        project.local_path = str(repo_dir)
        db.commit()

        repo_is_git = is_git_repo(repo_dir)
        if repo_is_git and head_sha:
            if not commit_exists(repo_dir, head_sha):
                raise RuntimeError(f"head_sha not found in repository: {head_sha}")
            checkout_ref(repo_dir, head_sha)

        resolved_head_sha = get_head_sha(repo_dir) if repo_is_git else None
        _set_job_commit_sha(db, job_id, resolved_head_sha)

        last_success = get_last_success_job_for_project(db, project_id)
        default_base_sha = last_success.commit_sha if last_success else None

        plan = _build_sync_plan(
            mode=mode,
            repo_is_git=repo_is_git,
            repo_dir=repo_dir,
            base_sha=base_sha,
            since_sha=since_sha,
            default_base_sha=default_base_sha,
            resolved_head_sha=resolved_head_sha,
            rename_detection=settings.sync_diff_rename_detection,
        )

        if not plan.replace and not plan.changed_files and not plan.deleted_files:
            mark_done(
                db,
                job_id,
                message=(
                    f"Sync completed: no file changes detected base={plan.base_sha} head={plan.head_sha} "
                    f"reason={plan.reason}"
                ),
            )
            write_audit(
                db,
                actor=actor,
                action="sync.done",
                project_id=project_id,
                detail=f"job={job_id} no_changes base={plan.base_sha} head={plan.head_sha}",
            )
            return

        indexing_result = indexing.scan_repo(
            project_id=project_id,
            repo_dir=repo_dir,
            include_files=plan.include_files,
        )
        chunks = chunking.build_chunks(project_id=project_id, repo_dir=repo_dir, symbols=indexing_result.symbols)

        if plan.replace:
            valid_chunks, dropped_chunks = _replace_project_index_snapshot(
                db=db,
                project_id=project_id,
                symbols=indexing_result.symbols,
                chunks=chunks,
            )
            vector_repo.delete_project(project_id)
        else:
            valid_chunks, dropped_chunks = _apply_incremental_snapshot(
                db=db,
                project_id=project_id,
                symbols=indexing_result.symbols,
                chunks=chunks,
                stale_files=plan.stale_files,
            )
            if plan.stale_files:
                vector_repo.delete_by_files(
                    project_id=project_id,
                    file_paths=plan.stale_files,
                    source_types={"code"},
                )

        if valid_chunks:
            vectors = embedding.embed_texts([chunk.content for chunk in valid_chunks])
            vector_repo.upsert_chunks(
                project_id=project_id,
                chunks=[
                    VectorChunk(
                        chunk_id=chunk.id,
                        project_id=chunk.project_id,
                        language=chunk.language,
                        symbol_type=chunk.symbol_type,
                        qualified_name=chunk.qualified_name,
                        file_path=chunk.file_path,
                        start_line=chunk.start_line,
                        end_line=chunk.end_line,
                        content=chunk.content,
                        symbol_id=chunk.symbol_id,
                        source_type=chunk.source_type or "code",
                        source_uri=chunk.source_uri,
                        title=chunk.title,
                        tags=chunk.tags,
                    )
                    for chunk in valid_chunks
                ],
                embeddings=vectors,
            )

        graph_repo.upsert_symbols(
            project_id=project_id,
            symbols=indexing_result.symbols,
            edges=indexing_result.edges,
            replace=plan.replace,
            touched_files=plan.changed_files,
            deleted_files=plan.deleted_files,
        )

        mark_done(
            db,
            job_id,
            message=(
                f"Sync completed: mode={plan.effective_mode} scanned_files={indexing_result.scanned_files} "
                f"symbols={len(indexing_result.symbols)} chunks={len(valid_chunks)} dropped_chunks={dropped_chunks} "
                f"base={plan.base_sha} head={plan.head_sha} reason={plan.reason}"
            ),
        )
        write_audit(
            db,
            actor=actor,
            action="sync.done",
            project_id=project_id,
            detail=(
                f"job={job_id} mode={plan.effective_mode} scanned_files={indexing_result.scanned_files} "
                f"symbols={len(indexing_result.symbols)} chunks={len(valid_chunks)} dropped_chunks={dropped_chunks} "
                f"base={plan.base_sha} head={plan.head_sha}"
            ),
        )
    except Exception as exc:  # pragma: no cover
        db.rollback()
        mark_failed(db, job_id, message=str(exc))
        write_audit(db, actor=actor, action="sync.failed", project_id=project_id, detail=f"job={job_id} err={exc}")
    finally:
        db.close()
        graph_repo.close()


def _build_sync_plan(
    mode: str,
    repo_is_git: bool,
    repo_dir,
    base_sha: str | None,
    since_sha: str | None,
    default_base_sha: str | None,
    resolved_head_sha: str | None,
    rename_detection: bool,
) -> SyncPlan:
    effective_mode = mode
    if mode == "full" or not repo_is_git:
        reason = "explicit_full" if mode == "full" else "non_git_repo_fallback_full"
        return SyncPlan(
            replace=True,
            effective_mode="full",
            changed_files=set(),
            deleted_files=set(),
            stale_files=set(),
            include_files=None,
            base_sha=None,
            head_sha=resolved_head_sha,
            reason=reason,
        )

    candidate_base = base_sha or since_sha or default_base_sha
    if not candidate_base:
        return SyncPlan(
            replace=True,
            effective_mode="full",
            changed_files=set(),
            deleted_files=set(),
            stale_files=set(),
            include_files=None,
            base_sha=None,
            head_sha=resolved_head_sha,
            reason="incremental_missing_base_fallback_full",
        )

    if not resolved_head_sha:
        return SyncPlan(
            replace=True,
            effective_mode="full",
            changed_files=set(),
            deleted_files=set(),
            stale_files=set(),
            include_files=None,
            base_sha=candidate_base,
            head_sha=None,
            reason="missing_head_sha_fallback_full",
        )

    if candidate_base == resolved_head_sha:
        return SyncPlan(
            replace=False,
            effective_mode="incremental",
            changed_files=set(),
            deleted_files=set(),
            stale_files=set(),
            include_files=set(),
            base_sha=candidate_base,
            head_sha=resolved_head_sha,
            reason="base_equals_head",
        )

    if not commit_exists(repo_dir, candidate_base):
        return SyncPlan(
            replace=True,
            effective_mode="full",
            changed_files=set(),
            deleted_files=set(),
            stale_files=set(),
            include_files=None,
            base_sha=candidate_base,
            head_sha=resolved_head_sha,
            reason="base_not_found_fallback_full",
        )

    try:
        changed_files, deleted_files = changed_files_between(
            repo_dir=repo_dir,
            base_sha=candidate_base,
            head_sha=resolved_head_sha,
            rename_detection=rename_detection,
        )
    except SCMError:
        return SyncPlan(
            replace=True,
            effective_mode="full",
            changed_files=set(),
            deleted_files=set(),
            stale_files=set(),
            include_files=None,
            base_sha=candidate_base,
            head_sha=resolved_head_sha,
            reason="diff_failed_fallback_full",
        )

    stale_files = set(changed_files) | set(deleted_files)
    return SyncPlan(
        replace=False,
        effective_mode="incremental",
        changed_files=set(changed_files),
        deleted_files=set(deleted_files),
        stale_files=stale_files,
        include_files=set(changed_files),
        base_sha=candidate_base,
        head_sha=resolved_head_sha,
        reason="git_diff_incremental",
    )


def _mock_sync(db: Session, project_id: str) -> None:
    symbol_id = str(uuid.uuid4())
    symbol = Symbol(
        id=symbol_id,
        project_id=project_id,
        language="python",
        symbol_type="function",
        qualified_name="mvp.placeholder.symbol",
        file_path="mvp/placeholder.py",
        start_line=1,
        end_line=3,
    )
    db.query(CodeChunk).filter(CodeChunk.project_id == project_id).delete()
    db.query(Symbol).filter(Symbol.project_id == project_id).delete()
    db.commit()

    db.add(symbol)
    db.commit()

    chunk = CodeChunk(
        project_id=project_id,
        symbol_id=symbol_id,
        language="python",
        symbol_type="function",
        qualified_name=symbol.qualified_name,
        file_path=symbol.file_path,
        source_type="code",
        source_uri=symbol.file_path,
        title=symbol.qualified_name,
        start_line=symbol.start_line,
        end_line=symbol.end_line,
        content=(
            "file=mvp/placeholder.py\n"
            "symbol=mvp.placeholder.symbol\n"
            "kind=function\n"
            "lines=1-3\n\n"
            "def placeholder():\n"
            "    return 'ok'\n"
        ),
        embedding_model=get_settings().embedding_model,
    )
    db.add(chunk)
    db.commit()


def _replace_project_index_snapshot(
    db: Session,
    project_id: str,
    symbols: list[Symbol],
    chunks: list[CodeChunk],
) -> tuple[list[CodeChunk], int]:
    db.query(CodeChunk).filter(CodeChunk.project_id == project_id).delete()
    db.query(Symbol).filter(Symbol.project_id == project_id).delete()
    db.commit()

    if symbols:
        db.add_all(symbols)
        db.commit()

    valid_chunks, dropped = _filter_valid_chunks(db=db, project_id=project_id, chunks=chunks)
    if valid_chunks:
        db.add_all(valid_chunks)
        db.commit()
    return valid_chunks, dropped


def _apply_incremental_snapshot(
    db: Session,
    project_id: str,
    symbols: list[Symbol],
    chunks: list[CodeChunk],
    stale_files: set[str],
) -> tuple[list[CodeChunk], int]:
    if stale_files:
        stale_list = sorted(stale_files)
        db.query(CodeChunk).filter(CodeChunk.project_id == project_id, CodeChunk.file_path.in_(stale_list)).delete(
            synchronize_session=False
        )
        db.query(Symbol).filter(Symbol.project_id == project_id, Symbol.file_path.in_(stale_list)).delete(
            synchronize_session=False
        )
        db.commit()

    if symbols:
        db.add_all(symbols)
        db.commit()

    valid_chunks, dropped = _filter_valid_chunks(db=db, project_id=project_id, chunks=chunks)
    if valid_chunks:
        db.add_all(valid_chunks)
        db.commit()
    return valid_chunks, dropped


def _filter_valid_chunks(
    db: Session,
    project_id: str,
    chunks: list[CodeChunk],
) -> tuple[list[CodeChunk], int]:
    existing_symbol_ids = {
        row[0]
        for row in db.query(Symbol.id).filter(Symbol.project_id == project_id).all()
    }
    valid_chunks = [
        chunk
        for chunk in chunks
        if chunk.symbol_id is None or chunk.symbol_id in existing_symbol_ids
    ]
    dropped = len(chunks) - len(valid_chunks)
    return valid_chunks, dropped


def _set_job_commit_sha(db: Session, job_id: str, commit_sha: str | None) -> None:
    if not commit_sha:
        return
    row = db.query(SyncJob).filter(SyncJob.id == job_id).first()
    if not row:
        return
    row.commit_sha = commit_sha
    db.commit()


def _project_lock(project_id: str) -> threading.Lock:
    with _SYNC_LOCKS_GUARD:
        lock = _SYNC_LOCKS.get(project_id)
        if lock is None:
            lock = threading.Lock()
            _SYNC_LOCKS[project_id] = lock
        return lock
