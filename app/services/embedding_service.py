from __future__ import annotations

import hashlib
import math

import httpx

from app.core.config import get_settings


class EmbeddingService:
    def __init__(self) -> None:
        self.settings = get_settings()

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        provider = self.settings.embedding_provider.strip().lower()
        if provider == "local_hash":
            return [self._local_hash_embed(text) for text in texts]
        if provider == "openai_compatible":
            return self._openai_compatible_embed_batched(texts)
        raise RuntimeError(f"Unsupported embedding provider: {self.settings.embedding_provider}")

    def embed_query(self, query: str) -> list[float]:
        vectors = self.embed_texts([query])
        return vectors[0]

    def _local_hash_embed(self, text: str) -> list[float]:
        dim = self.settings.embedding_dimensions
        vec = [0.0] * dim
        tokens = text.lower().split()
        if not tokens:
            return vec

        for idx, token in enumerate(tokens):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], byteorder="little") % dim
            sign = 1.0 if (digest[4] & 1) == 0 else -1.0
            weight = 1.0 + min(idx, 64) * 0.002
            vec[bucket] += sign * weight

        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            return vec
        return [v / norm for v in vec]

    def _openai_compatible_embed_batched(self, texts: list[str]) -> list[list[float]]:
        batch_size = self.settings.embedding_batch_size
        vectors: list[list[float]] = []
        for offset in range(0, len(texts), batch_size):
            vectors.extend(self._openai_compatible_embed(texts[offset : offset + batch_size]))
        return vectors

    def _openai_compatible_embed(self, texts: list[str]) -> list[list[float]]:
        if not self.settings.embedding_api_base:
            raise RuntimeError("embedding_api_base is required for openai_compatible provider")

        endpoint = self.settings.embedding_api_base.rstrip("/") + "/embeddings"
        headers = {"Content-Type": "application/json"}
        if self.settings.embedding_api_key:
            headers["Authorization"] = f"Bearer {self.settings.embedding_api_key}"

        payload = {
            "model": self.settings.embedding_model,
            "input": texts,
        }

        with httpx.Client(timeout=self.settings.embedding_timeout_sec) as client:
            resp = client.post(endpoint, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        if "data" not in data:
            raise RuntimeError("Embedding response missing 'data' field")

        # Keep output order aligned to input indexes.
        items = sorted(data["data"], key=lambda x: x.get("index", 0))
        vectors = [item["embedding"] for item in items]
        if len(vectors) != len(texts):
            raise RuntimeError("Embedding response size mismatch")
        return vectors
