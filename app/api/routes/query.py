from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import ActorContext, get_actor_context, require_project_role
from app.db.session import get_db
from app.schemas.query import QueryRequest, QueryResponse
from app.services.retrieval_service import RetrievalService

router = APIRouter(tags=["query"])


@router.post("/query", response_model=QueryResponse)
def query_endpoint(
    payload: QueryRequest,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> QueryResponse:
    require_project_role(db=db, project_id=payload.project_id, actor=actor, min_role="viewer")

    retrieval = RetrievalService()
    answer, sources, contexts, citations, meta = retrieval.answer(
        db=db,
        project_id=payload.project_id,
        question=payload.question,
        top_k=payload.top_k,
        actor_user_id=actor.user_id,
        actor_role=actor.role,
        source_types=payload.source_types,
        knowledge_scope=payload.knowledge_scope,
        filters=payload.filters,
        need_citations=payload.need_citations,
    )
    return QueryResponse(
        answer=answer,
        sources=sources,
        contexts=contexts,
        citations=citations,
        retrieval_meta=meta,
    )
