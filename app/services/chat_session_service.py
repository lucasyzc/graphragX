from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import ChatMessage, ChatSession
from app.schemas.chat import ChatMessageResponse, ChatSessionCreate, ChatSessionResponse, ChatSessionUpdate


def create_chat_session(
    db: Session,
    owner_user_id: str,
    payload: ChatSessionCreate,
) -> ChatSession:
    now = datetime.utcnow()
    session = ChatSession(
        owner_user_id=owner_user_id,
        title=payload.title or "新会话",
        default_project_id=payload.default_project_id,
        archived=False,
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def list_chat_sessions(
    db: Session,
    owner_user_id: str,
    limit: int,
    offset: int,
    project_id: str | None = None,
) -> tuple[list[ChatSession], int]:
    query = db.query(ChatSession).filter(ChatSession.owner_user_id == owner_user_id)
    if project_id:
        query = query.filter(ChatSession.default_project_id == project_id)
    total = query.count()
    rows = (
        query.order_by(
            ChatSession.archived.asc(),
            ChatSession.last_message_at.is_(None),
            ChatSession.last_message_at.desc(),
            ChatSession.updated_at.desc(),
            ChatSession.created_at.desc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    return rows, total


def get_chat_session_for_owner(db: Session, session_id: str, owner_user_id: str) -> ChatSession | None:
    return (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.owner_user_id == owner_user_id)
        .first()
    )


def update_chat_session(
    db: Session,
    session: ChatSession,
    payload: ChatSessionUpdate,
    fields_set: set[str] | None = None,
) -> ChatSession:
    resolved_fields = fields_set or set(payload.model_fields_set)

    if "title" in resolved_fields:
        session.title = payload.title
    if "default_project_id" in resolved_fields:
        session.default_project_id = payload.default_project_id
    if "archived" in resolved_fields:
        session.archived = payload.archived
    session.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(session)
    return session


def list_chat_messages(
    db: Session,
    session_id: str,
    limit: int,
    before: datetime | None = None,
) -> tuple[list[ChatMessage], int]:
    query = db.query(ChatMessage).filter(ChatMessage.session_id == session_id)
    if before is not None:
        query = query.filter(ChatMessage.created_at < before)
    total = query.count()
    rows = query.order_by(ChatMessage.created_at.desc()).limit(limit).all()
    rows.reverse()
    return rows, total


def create_chat_message(
    db: Session,
    session_id: str,
    role: str,
    content: str,
    effective_project_id: str | None,
    query_request: dict[str, Any] | None = None,
    query_response: dict[str, Any] | None = None,
) -> ChatMessage:
    row = ChatMessage(
        session_id=session_id,
        role=role,
        content=content,
        effective_project_id=effective_project_id,
        query_request_json=_to_json_text(query_request),
        query_response_json=_to_json_text(query_response),
    )
    db.add(row)
    db.flush()
    return row


def touch_chat_session_after_message(db: Session, session: ChatSession, ts: datetime | None = None) -> None:
    now = ts or datetime.utcnow()
    session.last_message_at = now
    session.updated_at = now
    db.flush()


def chat_message_to_response(message: ChatMessage) -> ChatMessageResponse:
    return ChatMessageResponse(
        id=message.id,
        session_id=message.session_id,
        role=message.role,
        content=message.content,
        effective_project_id=message.effective_project_id,
        query_request=_from_json_text(message.query_request_json),
        query_response=_from_json_text(message.query_response_json),
        created_at=message.created_at,
    )


def chat_session_to_response(session: ChatSession) -> ChatSessionResponse:
    return ChatSessionResponse(
        id=session.id,
        owner_user_id=session.owner_user_id,
        title=session.title,
        default_project_id=session.default_project_id,
        project_id=session.default_project_id,
        archived=session.archived,
        created_at=session.created_at,
        updated_at=session.updated_at,
        last_message_at=session.last_message_at,
    )


def _to_json_text(payload: dict[str, Any] | None) -> str | None:
    if payload is None:
        return None
    return json.dumps(payload, ensure_ascii=False)


def _from_json_text(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None
