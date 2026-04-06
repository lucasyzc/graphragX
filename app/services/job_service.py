from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.db.models import SyncJob


def create_sync_job(db: Session, project_id: str, mode: str, commit_sha: str | None) -> SyncJob:
    job = SyncJob(project_id=project_id, mode=mode, commit_sha=commit_sha, status="queued")
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_job(db: Session, job_id: str) -> SyncJob | None:
    return db.query(SyncJob).filter(SyncJob.id == job_id).first()


def list_jobs(
    db: Session,
    project_id: str | None = None,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[SyncJob], int]:
    query = db.query(SyncJob)
    if project_id:
        query = query.filter(SyncJob.project_id == project_id)
    if status:
        query = query.filter(SyncJob.status == status)

    total = query.count()
    items = (
        query.order_by(SyncJob.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return items, total


def get_active_job_for_project(db: Session, project_id: str) -> SyncJob | None:
    return (
        db.query(SyncJob)
        .filter(
            SyncJob.project_id == project_id,
            SyncJob.status.in_(["queued", "running"]),
        )
        .order_by(SyncJob.created_at.desc())
        .first()
    )


def get_sync_status_for_project(db: Session, project_id: str) -> dict[str, SyncJob | int | None]:
    active_job = (
        db.query(SyncJob)
        .filter(
            SyncJob.project_id == project_id,
            SyncJob.status.in_(["queued", "running"]),
        )
        .order_by(SyncJob.created_at.desc())
        .first()
    )
    last_success_job = (
        db.query(SyncJob)
        .filter(SyncJob.project_id == project_id, SyncJob.status == "done")
        .order_by(SyncJob.finished_at.desc(), SyncJob.created_at.desc())
        .first()
    )
    last_failed_job = (
        db.query(SyncJob)
        .filter(SyncJob.project_id == project_id, SyncJob.status == "failed")
        .order_by(SyncJob.finished_at.desc(), SyncJob.created_at.desc())
        .first()
    )
    pending_count = (
        db.query(SyncJob)
        .filter(
            SyncJob.project_id == project_id,
            SyncJob.status.in_(["queued", "running"]),
        )
        .count()
    )
    return {
        "active_job": active_job,
        "last_success_job": last_success_job,
        "last_failed_job": last_failed_job,
        "pending_count": pending_count,
    }


def get_last_success_job_for_project(db: Session, project_id: str) -> SyncJob | None:
    return (
        db.query(SyncJob)
        .filter(SyncJob.project_id == project_id, SyncJob.status == "done")
        .order_by(SyncJob.finished_at.desc(), SyncJob.created_at.desc())
        .first()
    )


def fail_stale_active_job_for_project(db: Session, project_id: str, stale_minutes: int) -> SyncJob | None:
    active = get_active_job_for_project(db=db, project_id=project_id)
    if not active:
        return None

    now = datetime.utcnow()
    stale_before = now - timedelta(minutes=stale_minutes)

    if active.status == "running" and active.started_at and active.started_at < stale_before:
        active.status = "failed"
        active.message = f"Auto-failed stale running job after {stale_minutes} minutes"
        active.finished_at = now
        db.commit()
        return None

    if active.status == "queued" and active.created_at < stale_before:
        active.status = "failed"
        active.message = f"Auto-failed stale queued job after {stale_minutes} minutes"
        active.finished_at = now
        db.commit()
        return None

    return active


def mark_running(db: Session, job_id: str) -> None:
    job = get_job(db, job_id)
    if not job:
        return
    job.status = "running"
    job.started_at = datetime.utcnow()
    db.commit()


def mark_done(db: Session, job_id: str, message: str) -> None:
    job = get_job(db, job_id)
    if not job:
        return
    job.status = "done"
    job.message = message
    job.finished_at = datetime.utcnow()
    db.commit()


def mark_failed(db: Session, job_id: str, message: str) -> None:
    job = get_job(db, job_id)
    if not job:
        return
    job.status = "failed"
    job.message = message
    job.finished_at = datetime.utcnow()
    db.commit()
