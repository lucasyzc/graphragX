from dataclasses import dataclass


@dataclass
class VectorChunk:
    chunk_id: str
    project_id: str
    language: str
    symbol_type: str
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    content: str
    symbol_id: str | None = None
    document_id: str | None = None
    source_type: str = "code"
    source_uri: str | None = None
    title: str | None = None
    chunk_index: int | None = None
    tags: str | None = None
