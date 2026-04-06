from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import ActorContext, get_actor_context, require_project_role
from app.db.models import ProjectMember, SyncJob
from app.db.session import get_db
from app.schemas.job import JobListResponse, JobResponse
from app.services.job_service import get_job

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=JobListResponse)
def list_jobs_endpoint(
    project_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> JobListResponse:
    allowed_status = {"queued", "running", "done", "failed"}
    if status and status not in allowed_status:
        raise HTTPException(status_code=400, detail="Invalid status. Expected queued|running|done|failed")

    query = (
        db.query(SyncJob)
        .join(
            ProjectMember,
            (ProjectMember.project_id == SyncJob.project_id) & (ProjectMember.user_id == actor.user_id),
        )
    )

    if project_id:
        require_project_role(db=db, project_id=project_id, actor=actor, min_role="viewer")
        query = query.filter(SyncJob.project_id == project_id)
    if status:
        query = query.filter(SyncJob.status == status)

    total = query.count()
    rows = query.order_by(SyncJob.created_at.desc()).offset(offset).limit(limit).all()
    return JobListResponse(items=[JobResponse.model_validate(item) for item in rows], total=total)


@router.get("/{job_id}", response_model=JobResponse)
def get_job_status(
    job_id: str,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> JobResponse:
    job = get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    require_project_role(db=db, project_id=job.project_id, actor=actor, min_role="viewer")
    return JobResponse.model_validate(job)
