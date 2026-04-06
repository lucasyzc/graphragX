from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import ActorContext, get_actor_context, require_project_role
from app.db.session import get_db
from app.schemas.chat import (
    ChatMessageCreate,
    ChatMessageListResponse,
    ChatSessionCreate,
    ChatSessionListResponse,
    ChatSessionResponse,
    ChatSessionUpdate,
    ChatTurnResponse,
)
from app.services.chat_session_service import (
    chat_message_to_response,
    chat_session_to_response,
    create_chat_message,
    create_chat_session,
    get_chat_session_for_owner,
    list_chat_messages,
    list_chat_sessions,
    touch_chat_session_after_message,
    update_chat_session,
)
from app.services.retrieval_service import RetrievalService

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/sessions", response_model=ChatSessionListResponse)
def list_chat_sessions_endpoint(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    project_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> ChatSessionListResponse:
    if project_id:
        require_project_role(
            db=db,
            project_id=project_id,
            actor=actor,
            min_role="viewer",
        )
    rows, total = list_chat_sessions(
        db=db,
        owner_user_id=actor.user_id,
        limit=limit,
        offset=offset,
        project_id=project_id,
    )
    return ChatSessionListResponse(items=[chat_session_to_response(item) for item in rows], total=total)


@router.post("/sessions", response_model=ChatSessionResponse)
def create_chat_session_endpoint(
    payload: ChatSessionCreate,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> ChatSessionResponse:
    require_project_role(
        db=db,
        project_id=payload.default_project_id,
        actor=actor,
        min_role="viewer",
    )

    session = create_chat_session(db=db, owner_user_id=actor.user_id, payload=payload)
    return chat_session_to_response(session)


@router.patch("/sessions/{session_id}", response_model=ChatSessionResponse)
def update_chat_session_endpoint(
    session_id: str,
    payload: ChatSessionUpdate,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> ChatSessionResponse:
    session = get_chat_session_for_owner(db=db, session_id=session_id, owner_user_id=actor.user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")

    fields_set = set(payload.model_fields_set)
    if "default_project_id" in fields_set:
        require_project_role(
            db=db,
            project_id=payload.default_project_id,
            actor=actor,
            min_role="viewer",
        )

    updated = update_chat_session(db=db, session=session, payload=payload, fields_set=fields_set)
    return chat_session_to_response(updated)


@router.get("/sessions/{session_id}/messages", response_model=ChatMessageListResponse)
def list_chat_messages_endpoint(
    session_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    before: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> ChatMessageListResponse:
    session = get_chat_session_for_owner(db=db, session_id=session_id, owner_user_id=actor.user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")

    rows, total = list_chat_messages(db=db, session_id=session_id, limit=limit, before=before)
    return ChatMessageListResponse(items=[chat_message_to_response(item) for item in rows], total=total)


@router.post("/sessions/{session_id}/messages", response_model=ChatTurnResponse)
def create_chat_turn_endpoint(
    session_id: str,
    payload: ChatMessageCreate,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> ChatTurnResponse:
    session = get_chat_session_for_owner(db=db, session_id=session_id, owner_user_id=actor.user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")

    effective_project_id = session.default_project_id
    if not effective_project_id:
        raise HTTPException(status_code=400, detail="effective_project_id is required")

    deprecation_warnings: list[str] = []
    if payload.project_id_override is not None:
        if payload.project_id_override != effective_project_id:
            deprecation_warnings.append(
                "project_id_override is deprecated and ignored; session.default_project_id is always used."
            )
        else:
            deprecation_warnings.append(
                "project_id_override is deprecated; remove this field and rely on session.default_project_id."
            )

    require_project_role(
        db=db,
        project_id=effective_project_id,
        actor=actor,
        min_role="viewer",
    )

    retrieval = RetrievalService()
    answer, sources, contexts, citations, meta = retrieval.answer(
        db=db,
        project_id=effective_project_id,
        question=payload.content,
        top_k=payload.top_k,
        actor_user_id=actor.user_id,
        actor_role=actor.role,
        source_types=payload.source_types,
        knowledge_scope=payload.knowledge_scope,
        filters=payload.filters,
        need_citations=payload.need_citations,
    )

    user_request = {
        "content": payload.content,
        "project_id_override": payload.project_id_override,
        "top_k": payload.top_k,
        "source_types": payload.source_types,
        "knowledge_scope": payload.knowledge_scope,
        "filters": payload.filters,
        "need_citations": payload.need_citations,
    }
    assistant_response = {
        "answer": answer,
        "sources": [item.model_dump() for item in sources],
        "contexts": [item.model_dump() for item in contexts],
        "citations": [item.model_dump() for item in citations],
        "retrieval_meta": meta.model_dump(),
    }

    user_message = create_chat_message(
        db=db,
        session_id=session.id,
        role="user",
        content=payload.content,
        effective_project_id=effective_project_id,
        query_request=user_request,
    )
    assistant_message = create_chat_message(
        db=db,
        session_id=session.id,
        role="assistant",
        content=answer,
        effective_project_id=effective_project_id,
        query_response=assistant_response,
    )
    touch_chat_session_after_message(db=db, session=session)
    db.commit()
    db.refresh(user_message)
    db.refresh(assistant_message)

    return ChatTurnResponse(
        user_message=chat_message_to_response(user_message),
        assistant_message=chat_message_to_response(assistant_message),
        sources=sources,
        contexts=contexts,
        citations=citations,
        retrieval_meta=meta,
        deprecation_warnings=deprecation_warnings,
    )
