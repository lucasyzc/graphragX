from __future__ import annotations

from datetime import datetime
import re

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import CodeChunk, DocumentChunk, Project, ProjectMemory
from app.graph.repository import GraphRepository
from app.schemas.query import QueryCitation, QueryContext, QuerySource, RetrievalMeta
from app.services.chat_service import ChatService
from app.services.embedding_service import EmbeddingService
from app.services.knowledge_service import query_document_chunks_for_keyword
from app.vector.repository import VectorRepository

TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class RetrievalService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.vector_repo = VectorRepository()
        self.embedding = EmbeddingService()
        self.chat = ChatService()

    def answer(
        self,
        db: Session,
        project_id: str,
        question: str,
        top_k: int | None = None,
        actor_user_id: str | None = None,
        actor_role: str = "viewer",
        source_types: list[str] | None = None,
        knowledge_scope: str = "auto",
        filters: dict | None = None,
        need_citations: bool = True,
    ) -> tuple[str, list[QuerySource], list[QueryContext], list[QueryCitation], RetrievalMeta]:
        del actor_user_id  # reserved for future user-level ACL extensions

        resolved_top_k = top_k or self.settings.default_top_k
        candidate_limit = min(200, resolved_top_k * self.settings.retrieval_candidate_multiplier)
        context_limit = min(self.settings.retrieval_context_limit, candidate_limit)
        resolved_types = self._resolve_source_types(question, source_types, knowledge_scope)
        query_filters = self._normalize_filters(filters)

        query_vector = self.embedding.embed_query(question)
        vector_hits = self.vector_repo.query(
            project_id=project_id,
            query_vector=query_vector,
            top_k=candidate_limit,
            source_types=resolved_types,
        )

        vector_contexts: list[dict] = [self._vector_hit_to_context(item) for item in vector_hits]
        keyword_contexts = self._keyword_contexts(
            db=db,
            project_id=project_id,
            question=question,
            actor_role=actor_role,
            source_types=resolved_types,
            query_filters=query_filters,
            limit=self.settings.retrieval_keyword_limit,
        )

        combined_contexts: list[dict] = vector_contexts + keyword_contexts
        seed_symbol_ids = {
            str(ctx["symbol_id"])
            for ctx in combined_contexts
            if ctx.get("source_type") == "code" and ctx.get("symbol_id")
        }

        graph_expanded_count = 0
        if "code" in resolved_types:
            graph_expanded_count = self._append_graph_contexts(
                db=db,
                project_id=project_id,
                seed_symbol_ids=seed_symbol_ids,
                combined_contexts=combined_contexts,
            )

        reranked = self._rerank_contexts(question, combined_contexts)
        selected = reranked[:context_limit]

        sources = [
            QuerySource(kind=item["source_kind"], ref=item["ref"], score=float(item["score"]))
            for item in selected
        ]
        contexts = [
            QueryContext(
                source_kind=item["source_kind"],
                source_type=item.get("source_type"),
                symbol_id=item.get("symbol_id"),
                document_id=item.get("document_id"),
                chunk_id=item.get("chunk_id"),
                chunk_index=item.get("chunk_index"),
                qualified_name=item.get("qualified_name"),
                file_path=item.get("file_path"),
                source_uri=item.get("source_uri"),
                title=item.get("title"),
                start_line=item.get("start_line"),
                end_line=item.get("end_line"),
                tags=self._normalize_tags(item.get("tags")),
                score=float(item["score"]),
                snippet=str(item.get("snippet", ""))[:1200],
            )
            for item in selected
        ]
        citations = self._build_citations(selected) if need_citations else []

        project_instructions, project_memories = self._project_guidance(db=db, project_id=project_id)
        chat_result = self.chat.generate_answer(
            question=question,
            contexts=selected,
            project_instructions=project_instructions,
            project_memories=project_memories,
        )
        evidence_coverage = 0.0 if not selected else min(1.0, round(len(citations) / max(1, len(selected)), 4))
        meta = RetrievalMeta(
            vector_hits=len(vector_contexts),
            keyword_hits=len(keyword_contexts),
            graph_expanded=graph_expanded_count,
            reranked=len(reranked),
            fusion_selected=len(selected),
            selected_contexts=len(selected),
            evidence_coverage=evidence_coverage,
            answer_mode=chat_result.answer_mode,
            chat_model=chat_result.model,
            llm_provider=chat_result.provider,
            llm_wire_api=chat_result.wire_api,
            llm_error=chat_result.error,
        )
        return chat_result.answer, sources, contexts, citations, meta

    def _keyword_contexts(
        self,
        db: Session,
        project_id: str,
        question: str,
        actor_role: str,
        source_types: set[str],
        query_filters: dict,
        limit: int,
    ) -> list[dict]:
        if not self.settings.retrieval_enable_keyword:
            return []
        tokens = self._token_set(question)
        if not tokens:
            return []
        output: list[dict] = []
        each_limit = max(8, limit // 2)

        if "code" in source_types:
            code_rows = self._keyword_code_rows(
                db=db,
                project_id=project_id,
                tokens=tokens,
                limit=each_limit,
                query_filters=query_filters,
            )
            for score, row in code_rows:
                output.append(
                    {
                        "source_kind": "keyword",
                        "source_type": row.source_type or "code",
                        "symbol_id": row.symbol_id,
                        "qualified_name": row.qualified_name,
                        "file_path": row.file_path,
                        "source_uri": row.source_uri or row.file_path,
                        "title": row.title or row.qualified_name,
                        "start_line": row.start_line,
                        "end_line": row.end_line,
                        "tags": row.tags,
                        "snippet": row.content,
                        "score": float(score),
                        "ref": self._source_ref(row.file_path, row.start_line, row.end_line, row.qualified_name),
                    }
                )

        if source_types.intersection({"doc", "faq"}):
            doc_rows = query_document_chunks_for_keyword(
                db=db,
                project_id=project_id,
                actor_role=actor_role,
                tokens=tokens,
                limit=each_limit,
                tags=query_filters.get("tags"),
                source_uri=query_filters.get("source"),
            )
            for row in doc_rows:
                lexical = self._lexical_score(tokens, row.content)
                output.append(
                    {
                        "source_kind": "keyword",
                        "source_type": row.source_type or "doc",
                        "document_id": row.document_id,
                        "chunk_id": row.id,
                        "chunk_index": row.chunk_index,
                        "qualified_name": row.title,
                        "file_path": row.source_uri,
                        "source_uri": row.source_uri,
                        "title": row.title,
                        "start_line": 1,
                        "end_line": 1,
                        "tags": row.tags,
                        "snippet": row.content,
                        "score": float(lexical),
                        "ref": self._doc_ref(row),
                    }
                )

        return output[:limit]

    @staticmethod
    def _project_guidance(db: Session, project_id: str) -> tuple[str | None, list[str]]:
        project = db.query(Project).filter(Project.id == project_id).first()
        instructions = None
        if project and isinstance(project.instructions, str):
            normalized = project.instructions.strip()
            instructions = normalized or None

        memory_rows = (
            db.query(ProjectMemory)
            .filter(ProjectMemory.project_id == project_id, ProjectMemory.archived.is_(False))
            .order_by(ProjectMemory.updated_at.desc(), ProjectMemory.created_at.desc())
            .limit(20)
            .all()
        )
        memories = [row.content.strip() for row in memory_rows if isinstance(row.content, str) and row.content.strip()]
        return instructions, memories

    def _keyword_code_rows(
        self,
        db: Session,
        project_id: str,
        tokens: set[str],
        limit: int,
        query_filters: dict,
    ) -> list[tuple[float, CodeChunk]]:
        query = db.query(CodeChunk).filter(CodeChunk.project_id == project_id, CodeChunk.source_type == "code")
        if query_filters.get("source"):
            query = query.filter(CodeChunk.file_path.contains(query_filters["source"]))
        if query_filters.get("updated_after"):
            query = query.filter(CodeChunk.updated_at >= query_filters["updated_after"])
        if query_filters.get("tags"):
            for tag in sorted(query_filters["tags"]):
                query = query.filter(CodeChunk.tags.contains(tag))
        rows = query.limit(max(40, limit * 4)).all()
        scored: list[tuple[float, CodeChunk]] = []
        for row in rows:
            lexical = self._lexical_score(tokens, row.content)
            if lexical > 0:
                scored.append((lexical, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:limit]

    def _append_graph_contexts(
        self,
        db: Session,
        project_id: str,
        seed_symbol_ids: set[str],
        combined_contexts: list[dict],
    ) -> int:
        graph_expanded_count = 0
        graph_repo = GraphRepository()
        try:
            if seed_symbol_ids:
                related = graph_repo.related_symbols(
                    project_id=project_id,
                    seed_symbol_ids=seed_symbol_ids,
                    max_hops=self.settings.retrieval_graph_hops,
                    limit=self.settings.retrieval_graph_limit,
                )
                related_ids = [item["id"] for item in related if item.get("id") and item["id"] not in seed_symbol_ids]
                if related_ids:
                    rows = (
                        db.query(CodeChunk)
                        .filter(
                            CodeChunk.project_id == project_id,
                            CodeChunk.symbol_id.in_(related_ids),
                        )
                        .limit(self.settings.retrieval_graph_limit)
                        .all()
                    )
                    for row in rows:
                        combined_contexts.append(
                            {
                                "source_kind": "graph",
                                "source_type": "code",
                                "symbol_id": row.symbol_id,
                                "qualified_name": row.qualified_name,
                                "file_path": row.file_path,
                                "source_uri": row.source_uri or row.file_path,
                                "title": row.title or row.qualified_name,
                                "start_line": row.start_line,
                                "end_line": row.end_line,
                                "tags": row.tags,
                                "snippet": row.content,
                                "score": 0.18,
                                "ref": self._source_ref(
                                    row.file_path,
                                    row.start_line,
                                    row.end_line,
                                    row.qualified_name,
                                ),
                            }
                        )
                    graph_expanded_count = len(rows)
        except Exception:
            graph_expanded_count = 0
        finally:
            graph_repo.close()
        return graph_expanded_count

    def _vector_hit_to_context(self, hit: dict) -> dict:
        payload = hit.get("payload", {})
        file_path = payload.get("file_path")
        start_line = payload.get("start_line")
        end_line = payload.get("end_line")
        qualified_name = payload.get("qualified_name")
        source_type = str(payload.get("source_type") or "code")
        source_uri = payload.get("source_uri") or file_path
        title = payload.get("title") or qualified_name
        ref = (
            self._doc_ref_payload(payload)
            if source_type in {"doc", "faq"}
            else self._source_ref(file_path, start_line, end_line, qualified_name)
        )
        return {
            "source_kind": "vector",
            "source_type": source_type,
            "symbol_id": payload.get("symbol_id"),
            "document_id": payload.get("document_id"),
            "chunk_id": hit.get("chunk_id"),
            "chunk_index": payload.get("chunk_index"),
            "qualified_name": qualified_name,
            "file_path": file_path,
            "source_uri": source_uri,
            "title": title,
            "start_line": start_line,
            "end_line": end_line,
            "tags": payload.get("tags"),
            "snippet": payload.get("content", ""),
            "score": float(hit.get("score", 0.0)),
            "ref": ref,
        }

    def _rerank_contexts(self, question: str, contexts: list[dict]) -> list[dict]:
        dedup: dict[tuple, dict] = {}
        question_tokens = self._token_set(question)

        for ctx in contexts:
            key = (
                ctx.get("chunk_id"),
                ctx.get("symbol_id"),
                ctx.get("document_id"),
                ctx.get("file_path"),
                ctx.get("start_line"),
                ctx.get("end_line"),
            )
            lexical = self._lexical_score(question_tokens, ctx.get("snippet", ""))
            base = float(ctx.get("score", 0.0))
            if ctx.get("source_kind") == "graph":
                base *= 0.65
            if ctx.get("source_kind") == "keyword":
                base = max(base, lexical)
            rerank_score = round(0.65 * base + 0.35 * lexical, 6)

            candidate = dict(ctx)
            candidate["score"] = rerank_score

            current = dedup.get(key)
            if not current or rerank_score > float(current.get("score", 0.0)):
                dedup[key] = candidate

        return sorted(dedup.values(), key=lambda x: float(x.get("score", 0.0)), reverse=True)

    @staticmethod
    def _build_citations(contexts: list[dict]) -> list[QueryCitation]:
        out: list[QueryCitation] = []
        seen: set[tuple[str, str]] = set()
        for item in contexts:
            ref = str(item.get("ref") or "unknown")
            source_kind = str(item.get("source_kind") or "unknown")
            key = (source_kind, ref)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                QueryCitation(
                    source_kind=source_kind,
                    title=item.get("title"),
                    source_uri=item.get("source_uri"),
                    ref=ref,
                    score=float(item.get("score", 0.0)),
                )
            )
        return out

    @staticmethod
    def _source_ref(file_path: str | None, start_line: int | None, end_line: int | None, symbol: str | None) -> str:
        fp = file_path or "unknown"
        sl = start_line or 1
        el = end_line or sl
        sym = symbol or "unknown"
        return f"{fp}:{sl}-{el} ({sym})"

    @staticmethod
    def _doc_ref(row: DocumentChunk) -> str:
        return f"{row.source_uri}#chunk-{row.chunk_index} ({row.title})"

    @staticmethod
    def _doc_ref_payload(payload: dict) -> str:
        uri = payload.get("source_uri") or payload.get("file_path") or "unknown"
        title = payload.get("title") or payload.get("qualified_name") or "doc"
        chunk_index = payload.get("chunk_index")
        if isinstance(chunk_index, (int, float)):
            return f"{uri}#chunk-{int(chunk_index)} ({title})"
        return f"{uri} ({title})"

    @staticmethod
    def _token_set(text: str) -> set[str]:
        return {token.lower() for token in TOKEN_PATTERN.findall(text or "")}

    @staticmethod
    def _normalize_tags(raw: object) -> list[str]:
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
        if isinstance(raw, str) and raw.strip():
            return [item.strip() for item in raw.split(",") if item.strip()]
        return []

    def _lexical_score(self, question_tokens: set[str], snippet: str) -> float:
        if not question_tokens:
            return 0.0
        snippet_tokens = self._token_set(snippet)
        if not snippet_tokens:
            return 0.0
        overlap = question_tokens.intersection(snippet_tokens)
        return len(overlap) / max(1, len(question_tokens))

    @staticmethod
    def _resolve_source_types(
        question: str,
        source_types: list[str] | None,
        knowledge_scope: str,
    ) -> set[str]:
        if source_types:
            normalized = {str(item).strip().lower() for item in source_types if item}
            return normalized or {"code"}
        scope = (knowledge_scope or "auto").strip().lower()
        if scope == "code":
            return {"code"}
        if scope == "knowledge":
            return {"doc", "faq"}
        if scope == "hybrid":
            return {"code", "doc", "faq"}
        q = question.lower()
        if any(word in q for word in ["文档", "手册", "faq", "policy", "规范", "流程"]):
            return {"doc", "faq", "code"}
        return {"code", "doc"}

    @staticmethod
    def _normalize_filters(filters: dict | None) -> dict:
        payload = filters if isinstance(filters, dict) else {}
        tags_value = payload.get("tags")
        if isinstance(tags_value, list):
            tags = {str(tag).strip() for tag in tags_value if str(tag).strip()}
        elif isinstance(tags_value, str) and tags_value.strip():
            tags = {item.strip() for item in tags_value.split(",") if item.strip()}
        else:
            tags = set()

        updated_after = None
        raw_time = payload.get("updated_after") or payload.get("time")
        if isinstance(raw_time, str) and raw_time.strip():
            try:
                updated_after = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
            except ValueError:
                updated_after = None

        source = payload.get("source")
        source_text = str(source).strip() if source is not None else None
        if source_text == "":
            source_text = None
        return {
            "tags": tags,
            "updated_after": updated_after,
            "source": source_text,
        }
