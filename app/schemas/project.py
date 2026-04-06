from datetime import datetime
from typing import Literal
from urllib.parse import urlparse
import os

from pydantic import BaseModel, Field, model_validator

from app.schemas.job import JobResponse


def normalize_and_validate_repo_url(scm_provider: str, repo_url: str) -> str:
    normalized = repo_url.strip()
    if not normalized:
        raise ValueError("repo_url cannot be empty")

    if scm_provider == "local":
        if not os.path.isdir(normalized):
            raise ValueError("repo_url must be an existing local directory when scm_provider=local")
    else:
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("repo_url must be a valid http(s) URL when scm_provider is github/gitlab")
    return normalized


class ProjectCreate(BaseModel):
    name: str
    scm_provider: Literal["github", "gitlab", "local"]
    repo_url: str
    default_branch: str = "main"
    instructions: str | None = None

    @model_validator(mode="after")
    def validate_repo_url_by_provider(self) -> "ProjectCreate":
        self.repo_url = normalize_and_validate_repo_url(self.scm_provider, self.repo_url)
        if self.instructions is not None:
            normalized = self.instructions.strip()
            self.instructions = normalized or None
        return self


class ProjectUpdate(BaseModel):
    name: str | None = None
    repo_url: str | None = None
    default_branch: str | None = None
    instructions: str | None = None

    @model_validator(mode="after")
    def validate_any_field_present(self) -> "ProjectUpdate":
        if self.name is None and self.repo_url is None and self.default_branch is None and self.instructions is None:
            raise ValueError("At least one field must be provided")
        if self.instructions is not None:
            normalized = self.instructions.strip()
            self.instructions = normalized or None
        return self


class ProjectResponse(BaseModel):
    id: str
    name: str
    scm_provider: str
    repo_url: str
    default_branch: str
    local_path: str | None
    instructions: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ProjectSyncStatusResponse(BaseModel):
    active_job: JobResponse | None
    last_success_job: JobResponse | None
    last_failed_job: JobResponse | None
    pending_count: int


class ProjectMemoryCreate(BaseModel):
    content: str

    @model_validator(mode="after")
    def normalize_content(self) -> "ProjectMemoryCreate":
        normalized = self.content.strip()
        if not normalized:
            raise ValueError("content cannot be empty")
        self.content = normalized
        return self


class ProjectMemoryUpdate(BaseModel):
    content: str | None = None
    archived: bool | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> "ProjectMemoryUpdate":
        if self.content is None and self.archived is None:
            raise ValueError("At least one field must be provided")
        if self.content is not None:
            normalized = self.content.strip()
            if not normalized:
                raise ValueError("content cannot be empty")
            self.content = normalized
        return self


class ProjectMemoryResponse(BaseModel):
    id: str
    project_id: str
    content: str
    created_by: str
    archived: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectMemoryListResponse(BaseModel):
    items: list[ProjectMemoryResponse]
    total: int = Field(ge=0)
