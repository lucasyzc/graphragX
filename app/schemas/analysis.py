from pydantic import BaseModel


class ImpactRequest(BaseModel):
    project_id: str
    file_paths: list[str]


class ImpactResponse(BaseModel):
    changed_files: list[str]
    impacted_symbols: list[str]
    notes: str
