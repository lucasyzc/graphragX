from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.query import QueryCitation, QueryContext, QuerySource, RetrievalMeta


class ChatSessionCreate(BaseModel):
    title: str | None = None
    default_project_id: str

    @model_validator(mode="after")
    def normalize_fields(self) -> "ChatSessionCreate":
        if self.title is not None:
            normalized = self.title.strip()
            self.title = normalized or None
        normalized_project = self.default_project_id.strip()
        if not normalized_project:
            raise ValueError("default_project_id cannot be empty")
        self.default_project_id = normalized_project
        return self


class ChatSessionUpdate(BaseModel):
    title: str | None = None
    default_project_id: str | None = None
    archived: bool | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> "ChatSessionUpdate":
        if self.title is None and self.default_project_id is None and self.archived is None:
            raise ValueError("At least one field must be provided")
        if "default_project_id" in self.model_fields_set and self.default_project_id is None:
            raise ValueError("default_project_id cannot be null")

        if self.title is not None:
            normalized = self.title.strip()
            if not normalized:
                raise ValueError("title cannot be empty")
            self.title = normalized

        if self.default_project_id is not None:
            normalized_project = self.default_project_id.strip()
            if not normalized_project:
                raise ValueError("default_project_id cannot be empty")
            self.default_project_id = normalized_project

        return self


class ChatSessionResponse(BaseModel):
    id: str
    owner_user_id: str
    title: str
    default_project_id: str | None
    project_id: str | None
    archived: bool
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None

    model_config = {"from_attributes": True}


class ChatSessionListResponse(BaseModel):
    items: list[ChatSessionResponse]
    total: int = Field(ge=0)


class ChatMessageCreate(BaseModel):
    content: str
    project_id_override: str | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)
    source_types: list[Literal["code", "doc", "faq"]] | None = None
    knowledge_scope: Literal["auto", "code", "knowledge", "hybrid"] = "auto"
    filters: dict[str, Any] | None = None
    need_citations: bool = True

    @model_validator(mode="after")
    def normalize_payload(self) -> "ChatMessageCreate":
        normalized_content = self.content.strip()
        if not normalized_content:
            raise ValueError("content cannot be empty")
        self.content = normalized_content

        if self.project_id_override is not None:
            normalized_project = self.project_id_override.strip()
            self.project_id_override = normalized_project or None

        return self


class ChatMessageResponse(BaseModel):
    id: str
    session_id: str
    role: str
    content: str
    effective_project_id: str | None
    query_request: dict[str, Any] | None = None
    query_response: dict[str, Any] | None = None
    created_at: datetime


class ChatMessageListResponse(BaseModel):
    items: list[ChatMessageResponse]
    total: int = Field(ge=0)


class ChatTurnResponse(BaseModel):
    user_message: ChatMessageResponse
    assistant_message: ChatMessageResponse
    sources: list[QuerySource]
    contexts: list[QueryContext] | None = None
    citations: list[QueryCitation] | None = None
    retrieval_meta: RetrievalMeta | None = None
    deprecation_warnings: list[str] = Field(default_factory=list)
