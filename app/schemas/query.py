from typing import Literal

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    project_id: str
    question: str
    top_k: int | None = Field(default=None, ge=1, le=50)
    source_types: list[Literal["code", "doc", "faq"]] | None = None
    knowledge_scope: Literal["auto", "code", "knowledge", "hybrid"] = "auto"
    filters: dict | None = None
    need_citations: bool = True


class QuerySource(BaseModel):
    kind: str
    ref: str
    score: float


class QueryContext(BaseModel):
    source_kind: str
    source_type: str | None = None
    symbol_id: str | None = None
    document_id: str | None = None
    chunk_id: str | None = None
    chunk_index: int | None = None
    qualified_name: str | None = None
    file_path: str | None = None
    source_uri: str | None = None
    title: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    tags: list[str] = Field(default_factory=list)
    score: float
    snippet: str


class QueryCitation(BaseModel):
    source_kind: str
    title: str | None = None
    source_uri: str | None = None
    ref: str
    score: float


class RetrievalMeta(BaseModel):
    vector_hits: int
    keyword_hits: int = 0
    graph_expanded: int
    reranked: int
    fusion_selected: int = 0
    selected_contexts: int
    evidence_coverage: float = 0.0
    answer_mode: str
    chat_model: str | None = None
    llm_provider: str | None = None
    llm_wire_api: str | None = None
    llm_error: str | None = None


class QueryResponse(BaseModel):
    answer: str
    sources: list[QuerySource]
    contexts: list[QueryContext] | None = None
    citations: list[QueryCitation] | None = None
    retrieval_meta: RetrievalMeta | None = None
