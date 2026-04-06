from datetime import datetime

from pydantic import BaseModel


class SymbolResponse(BaseModel):
    id: str
    project_id: str
    language: str
    symbol_type: str
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    created_at: datetime

    model_config = {"from_attributes": True}
