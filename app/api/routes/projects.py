from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import ActorContext, get_actor_context, require_project_role
from app.core.config import get_settings
from app.db.session import get_db
from app.schemas.job import JobResponse, SyncRequest
from app.schemas.project import (
    ProjectCreate,
    ProjectMemoryCreate,
    ProjectMemoryListResponse,
    ProjectMemoryResponse,
    ProjectMemoryUpdate,
    ProjectResponse,
    ProjectSyncStatusResponse,
    ProjectUpdate,
)
from app.services.audit_service import write_audit
from app.services.job_service import create_sync_job, fail_stale_active_job_for_project, get_sync_status_for_project
from app.services.project_service import (
    create_project,
    create_project_memory,
    get_project,
    get_project_memory,
    list_project_memories,
    list_projects,
    update_project,
    update_project_memory,
)
from app.services.sync_service import run_sync_job

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectResponse])
def list_projects_endpoint(
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> list[ProjectResponse]:
    _ = actor
    rows = list_projects(db)
    return [ProjectResponse.model_validate(item) for item in rows]


@router.post("", response_model=ProjectResponse)
def create_project_endpoint(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> ProjectResponse:
    if actor.role not in {"editor", "admin"}:
        raise HTTPException(status_code=403, detail="Only editor/admin can create projects")
    project = create_project(db=db, payload=payload, actor=actor)
    return ProjectResponse.model_validate(project)


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project_endpoint(
    project_id: str,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> ProjectResponse:
    if actor.role not in {"editor", "admin"}:
        raise HTTPException(status_code=403, detail="Only editor/admin can update projects")
    project = get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    require_project_role(db=db, project_id=project_id, actor=actor, min_role="editor")
    try:
        updated = update_project(db=db, project=project, payload=payload, actor=actor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ProjectResponse.model_validate(updated)


@router.get("/{project_id}/sync-status", response_model=ProjectSyncStatusResponse)
def get_project_sync_status(
    project_id: str,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> ProjectSyncStatusResponse:
    project = get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    require_project_role(db=db, project_id=project_id, actor=actor, min_role="viewer")
    status = get_sync_status_for_project(db=db, project_id=project_id)
    return ProjectSyncStatusResponse(
        active_job=JobResponse.model_validate(status["active_job"]) if status["active_job"] else None,
        last_success_job=JobResponse.model_validate(status["last_success_job"])
        if status["last_success_job"]
        else None,
        last_failed_job=JobResponse.model_validate(status["last_failed_job"]) if status["last_failed_job"] else None,
        pending_count=int(status["pending_count"]),
    )


@router.post("/{project_id}/sync", response_model=JobResponse)
def trigger_sync(
    project_id: str,
    payload: SyncRequest,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> JobResponse:
    if actor.role not in {"editor", "admin"}:
        raise HTTPException(status_code=403, detail="Only editor/admin can trigger sync")
    project = get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    require_project_role(db=db, project_id=project_id, actor=actor, min_role="editor")
    settings = get_settings()
    active = fail_stale_active_job_for_project(
        db=db,
        project_id=project_id,
        stale_minutes=settings.sync_stale_minutes,
    )
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"Project already has active sync job: {active.id} ({active.status})",
        )

    requested_head = payload.head_sha or payload.commit_sha
    job = create_sync_job(db=db, project_id=project_id, mode=payload.mode, commit_sha=requested_head)
    write_audit(db, actor=actor.user_id, action="sync.create", project_id=project_id, detail=f"job={job.id}")

    bg.add_task(
        run_sync_job,
        job.id,
        project_id,
        payload.mode,
        actor.user_id,
        payload.base_sha,
        requested_head,
        payload.since_sha,
    )
    return JobResponse.model_validate(job)


@router.get("/{project_id}/memories", response_model=ProjectMemoryListResponse)
def list_project_memories_endpoint(
    project_id: str,
    include_archived: bool = Query(default=False),
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> ProjectMemoryListResponse:
    project = get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    require_project_role(db=db, project_id=project_id, actor=actor, min_role="viewer")
    rows = list_project_memories(db=db, project_id=project_id, include_archived=include_archived)
    return ProjectMemoryListResponse(items=[ProjectMemoryResponse.model_validate(item) for item in rows], total=len(rows))


@router.post("/{project_id}/memories", response_model=ProjectMemoryResponse)
def create_project_memory_endpoint(
    project_id: str,
    payload: ProjectMemoryCreate,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> ProjectMemoryResponse:
    if actor.role not in {"editor", "admin"}:
        raise HTTPException(status_code=403, detail="Only editor/admin can create project memory")
    project = get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    require_project_role(db=db, project_id=project_id, actor=actor, min_role="editor")
    row = create_project_memory(db=db, project_id=project_id, content=payload.content, actor=actor)
    return ProjectMemoryResponse.model_validate(row)


@router.patch("/{project_id}/memories/{memory_id}", response_model=ProjectMemoryResponse)
def update_project_memory_endpoint(
    project_id: str,
    memory_id: str,
    payload: ProjectMemoryUpdate,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> ProjectMemoryResponse:
    if actor.role not in {"editor", "admin"}:
        raise HTTPException(status_code=403, detail="Only editor/admin can update project memory")
    project = get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    require_project_role(db=db, project_id=project_id, actor=actor, min_role="editor")
    row = get_project_memory(db=db, project_id=project_id, memory_id=memory_id)
    if not row:
        raise HTTPException(status_code=404, detail="Project memory not found")
    updated = update_project_memory(
        db=db,
        row=row,
        content=payload.content,
        archived=payload.archived,
        actor=actor,
    )
    return ProjectMemoryResponse.model_validate(updated)
