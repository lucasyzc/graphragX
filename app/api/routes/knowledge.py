import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import ActorContext, get_actor_context, require_project_role
from app.db.models import KnowledgeJob, ProjectMember
from app.db.session import get_db
from app.schemas.knowledge import (
    KnowledgeJobListResponse,
    KnowledgeJobResponse,
    KnowledgeSourceCreate,
    KnowledgeSourceResponse,
    KnowledgeSourceUpdate,
    KnowledgeSyncRequest,
)
from app.services.audit_service import write_audit
from app.services.knowledge_service import (
    create_knowledge_job,
    create_source,
    get_source,
    list_knowledge_jobs,
    list_sources,
    run_knowledge_sync_job,
    update_source,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("/sources", response_model=list[KnowledgeSourceResponse])
def list_knowledge_sources_endpoint(
    project_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> list[KnowledgeSourceResponse]:
    if project_id:
        require_project_role(db=db, project_id=project_id, actor=actor, min_role="viewer")
        rows = list_sources(db=db, project_id=project_id)
    else:
        allowed_project_ids = {
            row[0]
            for row in db.query(ProjectMember.project_id).filter(ProjectMember.user_id == actor.user_id).all()
        }
        if not allowed_project_ids:
            return []
        rows = [row for row in list_sources(db=db, project_id=None) if row.project_id in allowed_project_ids]
    return [_to_source_response(item) for item in rows]


@router.post("/sources", response_model=KnowledgeSourceResponse)
def create_knowledge_source_endpoint(
    payload: KnowledgeSourceCreate,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> KnowledgeSourceResponse:
    if actor.role not in {"editor", "admin"}:
        raise HTTPException(status_code=403, detail="Only editor/admin can create knowledge sources")
    require_project_role(db=db, project_id=payload.project_id, actor=actor, min_role="editor")
    row = create_source(db=db, payload=payload)
    write_audit(
        db=db,
        actor=actor.user_id,
        action="knowledge.source.create",
        project_id=row.project_id,
        detail=f"source={row.id}",
    )
    return _to_source_response(row)


@router.patch("/sources/{source_id}", response_model=KnowledgeSourceResponse)
def update_knowledge_source_endpoint(
    source_id: str,
    payload: KnowledgeSourceUpdate,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> KnowledgeSourceResponse:
    row = get_source(db=db, source_id=source_id)
    if not row:
        raise HTTPException(status_code=404, detail="Knowledge source not found")
    require_project_role(db=db, project_id=row.project_id, actor=actor, min_role="editor")
    updated = update_source(db=db, source=row, payload=payload)
    write_audit(
        db=db,
        actor=actor.user_id,
        action="knowledge.source.update",
        project_id=updated.project_id,
        detail=f"source={updated.id}",
    )
    return _to_source_response(updated)


@router.post("/sources/{source_id}/sync", response_model=KnowledgeJobResponse)
def trigger_knowledge_sync_endpoint(
    source_id: str,
    payload: KnowledgeSyncRequest,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> KnowledgeJobResponse:
    if actor.role not in {"editor", "admin"}:
        raise HTTPException(status_code=403, detail="Only editor/admin can trigger knowledge sync")
    source = get_source(db=db, source_id=source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Knowledge source not found")
    require_project_role(db=db, project_id=source.project_id, actor=actor, min_role="editor")

    active = (
        db.query(KnowledgeJob)
        .filter(
            KnowledgeJob.source_id == source_id,
            KnowledgeJob.status.in_(["queued", "running"]),
        )
        .order_by(KnowledgeJob.created_at.desc())
        .first()
    )
    if active:
        raise HTTPException(status_code=409, detail=f"Knowledge source already has active job: {active.id}")

    job = create_knowledge_job(db=db, project_id=source.project_id, source_id=source.id, mode=payload.mode)
    write_audit(
        db=db,
        actor=actor.user_id,
        action="knowledge.sync.create",
        project_id=source.project_id,
        detail=f"job={job.id} source={source.id}",
    )
    bg.add_task(run_knowledge_sync_job, job.id, source.id, payload.mode, actor.user_id)
    return KnowledgeJobResponse.model_validate(job)


@router.get("/jobs", response_model=KnowledgeJobListResponse)
def list_knowledge_jobs_endpoint(
    project_id: str | None = Query(default=None),
    source_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> KnowledgeJobListResponse:
    if project_id:
        require_project_role(db=db, project_id=project_id, actor=actor, min_role="viewer")

    items, total = list_knowledge_jobs(
        db=db,
        project_id=project_id,
        source_id=source_id,
        status=status,
        limit=limit,
        offset=offset,
    )

    if not project_id:
        allowed_project_ids = {
            row[0]
            for row in db.query(ProjectMember.project_id).filter(ProjectMember.user_id == actor.user_id).all()
        }
        items = [item for item in items if item.project_id in allowed_project_ids]
        total = len(items)

    return KnowledgeJobListResponse(items=[KnowledgeJobResponse.model_validate(item) for item in items], total=total)


def _to_source_response(row) -> KnowledgeSourceResponse:
    tags = []
    raw = (row.config_json or "").strip()
    if raw:
        try:
            payload = json.loads(raw)
            raw_tags = payload.get("tags", []) if isinstance(payload, dict) else []
            if isinstance(raw_tags, list):
                tags = [str(tag) for tag in raw_tags]
        except json.JSONDecodeError:
            tags = []
    return KnowledgeSourceResponse(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        source_type=row.source_type,
        source_uri=row.source_uri,
        enabled=bool(row.enabled),
        tags=tags,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
