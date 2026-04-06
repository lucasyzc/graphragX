from dataclasses import dataclass, field
from typing import Literal

EdgeType = Literal["CALLS", "DEFINES", "REFERENCES", "IMPORTS", "INHERITS", "FLOWS_TO"]


@dataclass
class SymbolIR:
    symbol_id: str
    project_id: str
    language: str
    symbol_type: str
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int


@dataclass
class EdgeIR:
    edge_type: EdgeType
    from_symbol_id: str
    to_symbol_id: str | None = None
    to_qualified_name: str | None = None


@dataclass
class FileIR:
    project_id: str
    language: str
    file_path: str
    symbols: list[SymbolIR] = field(default_factory=list)
    edges: list[EdgeIR] = field(default_factory=list)
