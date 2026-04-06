from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class KnowledgeSourceCreate(BaseModel):
    project_id: str
    name: str
    source_type: Literal["local_dir", "http"]
    source_uri: str
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True


class KnowledgeSourceUpdate(BaseModel):
    name: str | None = None
    source_uri: str | None = None
    tags: list[str] | None = None
    enabled: bool | None = None


class KnowledgeSourceResponse(BaseModel):
    id: str
    project_id: str
    name: str
    source_type: str
    source_uri: str
    enabled: bool
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeSyncRequest(BaseModel):
    mode: Literal["full", "incremental"] = "incremental"


class KnowledgeJobResponse(BaseModel):
    id: str
    project_id: str
    source_id: str
    mode: str
    status: str
    message: str | None
    scanned_count: int
    indexed_count: int
    skipped_count: int
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeJobListResponse(BaseModel):
    items: list[KnowledgeJobResponse]
    total: int = Field(ge=0)
