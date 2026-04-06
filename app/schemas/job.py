from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SyncRequest(BaseModel):
    mode: Literal["full", "incremental"] = "incremental"
    commit_sha: str | None = None
    base_sha: str | None = None
    head_sha: str | None = None
    since_sha: str | None = None


class JobResponse(BaseModel):
    id: str
    project_id: str
    mode: str
    commit_sha: str | None
    status: str
    message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class JobListResponse(BaseModel):
    items: list[JobResponse]
    total: int = Field(ge=0)
