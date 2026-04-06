import time

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from app.core.config import get_settings
from app.vector.types import VectorChunk


class VectorRepository:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.client: QdrantClient | None = None
        self._collection_ready = False
        if self.settings.enable_external_stores:
            self.client = QdrantClient(url=self.settings.qdrant_url, timeout=self.settings.qdrant_timeout_sec)

    def upsert_chunks(self, project_id: str, chunks: list[VectorChunk], embeddings: list[list[float]]) -> None:
        if not self.settings.enable_external_stores or not self.client:
            return
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings size mismatch")

        self._ensure_collection()
        batch_size = self.settings.qdrant_upsert_batch_size
        max_retries = self.settings.qdrant_upsert_max_retries
        for offset in range(0, len(chunks), batch_size):
            batch_chunks = chunks[offset : offset + batch_size]
            batch_vectors = embeddings[offset : offset + batch_size]
            points: list[qmodels.PointStruct] = []
            for chunk, vector in zip(batch_chunks, batch_vectors):
                points.append(
                    qmodels.PointStruct(
                        id=chunk.chunk_id,
                        vector=vector,
                        payload={
                            "project_id": chunk.project_id,
                            "symbol_id": chunk.symbol_id,
                            "document_id": chunk.document_id,
                            "language": chunk.language,
                            "symbol_type": chunk.symbol_type,
                            "qualified_name": chunk.qualified_name,
                            "file_path": chunk.file_path,
                            "source_type": chunk.source_type,
                            "source_uri": chunk.source_uri,
                            "title": chunk.title,
                            "chunk_index": chunk.chunk_index,
                            "tags": chunk.tags,
                            "start_line": chunk.start_line,
                            "end_line": chunk.end_line,
                            "content": chunk.content,
                        },
                    )
                )
            if not points:
                continue

            self._call_with_retries(
                lambda: self.client.upsert(collection_name=self.settings.qdrant_collection, points=points, wait=True)
            )

    def delete_project(self, project_id: str) -> None:
        if not self.settings.enable_external_stores or not self.client:
            return
        self._ensure_collection()
        project_filter = qmodels.Filter(
            must=[qmodels.FieldCondition(key="project_id", match=qmodels.MatchValue(value=project_id))]
        )
        self._call_with_retries(
            lambda: self.client.delete(
                collection_name=self.settings.qdrant_collection,
                points_selector=project_filter,
                wait=True,
            )
        )

    def delete_by_files(
        self,
        project_id: str,
        file_paths: set[str],
        source_types: set[str] | None = None,
    ) -> None:
        if not self.settings.enable_external_stores or not self.client:
            return
        if not file_paths:
            return
        self._ensure_collection()
        batch_size = self.settings.qdrant_upsert_batch_size
        sorted_paths = sorted(file_paths)
        normalized_source_types = sorted({str(item).strip().lower() for item in (source_types or set()) if item})
        for offset in range(0, len(sorted_paths), batch_size):
            batch_paths = sorted_paths[offset : offset + batch_size]
            must_conditions: list[qmodels.FieldCondition] = [
                qmodels.FieldCondition(key="project_id", match=qmodels.MatchValue(value=project_id)),
                qmodels.FieldCondition(key="file_path", match=qmodels.MatchAny(any=batch_paths)),
            ]
            if normalized_source_types:
                must_conditions.append(
                    qmodels.FieldCondition(key="source_type", match=qmodels.MatchAny(any=normalized_source_types))
                )
            file_filter = qmodels.Filter(must=must_conditions)
            self._call_with_retries(
                lambda: self.client.delete(
                    collection_name=self.settings.qdrant_collection,
                    points_selector=file_filter,
                    wait=True,
                )
            )

    def query(
        self,
        project_id: str,
        query_vector: list[float],
        top_k: int,
        source_types: set[str] | None = None,
    ) -> list[dict]:
        if not self.settings.enable_external_stores or not self.client:
            return []

        self._ensure_collection()
        must_conditions: list[qmodels.FieldCondition] = [
            qmodels.FieldCondition(key="project_id", match=qmodels.MatchValue(value=project_id))
        ]
        normalized_source_types = sorted({str(item).strip().lower() for item in (source_types or set()) if item})
        if normalized_source_types:
            must_conditions.append(
                qmodels.FieldCondition(key="source_type", match=qmodels.MatchAny(any=normalized_source_types))
            )
        project_filter = qmodels.Filter(must=must_conditions)

        result = self.client.query_points(
            collection_name=self.settings.qdrant_collection,
            query=query_vector,
            query_filter=project_filter,
            limit=top_k,
            with_payload=True,
            with_vectors=False,
        )
        points = result.points or []
        return [
            {
                "chunk_id": str(point.id),
                "score": float(point.score),
                "payload": point.payload or {},
            }
            for point in points
        ]

    def _ensure_collection(self) -> None:
        if self._collection_ready or not self.client:
            return
        try:
            self.client.get_collection(self.settings.qdrant_collection)
        except Exception:
            self.client.create_collection(
                collection_name=self.settings.qdrant_collection,
                vectors_config=qmodels.VectorParams(
                    size=self.settings.embedding_dimensions,
                    distance=qmodels.Distance.COSINE,
                ),
            )
        self._collection_ready = True

    def _call_with_retries(self, func) -> None:
        max_retries = self.settings.qdrant_upsert_max_retries
        attempt = 0
        while True:
            try:
                func()
                return
            except Exception:
                if attempt >= max_retries:
                    raise
                time.sleep(min(0.5 * (2**attempt), 5.0))
                attempt += 1
