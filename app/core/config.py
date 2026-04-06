from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "GraphRAGX"
    app_env: str = "dev"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/codegraphrag"

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "neo4j"

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "code_chunks"
    qdrant_timeout_sec: int = Field(default=10, ge=1, le=120)
    qdrant_upsert_batch_size: int = Field(default=256, ge=1, le=5000)
    qdrant_upsert_max_retries: int = Field(default=2, ge=0, le=10)

    enable_external_stores: bool = False
    require_neo4j: bool = True
    require_qdrant: bool = True
    default_top_k: int = Field(default=8, ge=1, le=50)
    retrieval_candidate_multiplier: int = Field(default=3, ge=1, le=10)
    retrieval_graph_hops: int = Field(default=2, ge=1, le=3)
    retrieval_graph_limit: int = Field(default=80, ge=1, le=1000)
    retrieval_context_limit: int = Field(default=8, ge=1, le=50)
    retrieval_keyword_limit: int = Field(default=80, ge=1, le=500)
    retrieval_enable_keyword: bool = True

    embedding_provider: str = "local_hash"
    embedding_model: str = "codegraphrag-local-v1"
    embedding_dimensions: int = Field(default=384, ge=64, le=3072)
    embedding_api_base: str | None = None
    embedding_api_key: str | None = None
    embedding_timeout_sec: int = Field(default=30, ge=3, le=120)
    embedding_batch_size: int = Field(default=32, ge=1, le=256)

    chunk_max_lines: int = Field(default=120, ge=10, le=1000)
    chunk_max_chars: int = Field(default=4000, ge=500, le=20000)

    workspace_repos_dir: str = ".workspace/repos"
    index_max_files: int = Field(default=20000, ge=100, le=200000)
    sync_mock_mode: bool = False
    sync_stale_minutes: int = Field(default=120, ge=5, le=1440)
    sync_diff_rename_detection: bool = True
    enable_knowledge_base: bool = False
    knowledge_supported_exts: str = "md,txt,html,pdf,docx,json,jsonl"
    knowledge_chunk_chars: int = Field(default=1200, ge=200, le=8000)
    knowledge_chunk_overlap: int = Field(default=120, ge=0, le=1000)

    chat_provider: str = "none"
    chat_model: str = "gpt-4o-mini"
    chat_api_base: str | None = None
    chat_api_key: str | None = None

    openai_api_key: str | None = None
    openai_model: str | None = None
    openai_base_url: str | None = None
    openai_wire_api: str = "chat_completions"
    chat_temperature: float = Field(default=0.2, ge=0.0, le=1.5)
    chat_max_tokens: int = Field(default=700, ge=64, le=4096)

    def resolved_chat_provider(self) -> str:
        provider = (self.chat_provider or "").strip().lower()
        if provider in {"", "none", "disabled"} and self.resolved_chat_api_base() and self.resolved_chat_api_key():
            return "openai_compatible"
        return provider or "none"

    def resolved_chat_api_base(self) -> str | None:
        base = (self.chat_api_base or "").strip()
        if not base:
            base = (self.openai_base_url or "").strip()
        return base or None

    def resolved_chat_api_key(self) -> str | None:
        key = (self.chat_api_key or "").strip()
        if not key:
            key = (self.openai_api_key or "").strip()
        return key or None

    def resolved_chat_model(self) -> str:
        # Prefer OPENAI_MODEL when provided, otherwise fall back to CHAT_MODEL/default.
        openai_model = (self.openai_model or "").strip()
        if openai_model:
            return openai_model
        return (self.chat_model or "").strip() or "gpt-4o-mini"

    def resolved_openai_wire_api(self) -> str:
        wire = (self.openai_wire_api or "").strip().lower()
        if wire in {"responses", "response"}:
            return "responses"
        if wire in {"chat", "chat_completions", "chat-completions", "chat/completions"}:
            return "chat_completions"
        return "chat_completions"


@lru_cache
def get_settings() -> Settings:
    return Settings()
