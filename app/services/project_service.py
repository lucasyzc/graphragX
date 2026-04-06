from datetime import datetime

from sqlalchemy.orm import Session

from app.api.deps import ActorContext
from app.db.models import Project, ProjectMember, ProjectMemory
from app.schemas.project import ProjectCreate, ProjectUpdate, normalize_and_validate_repo_url
from app.services.audit_service import write_audit


def create_project(db: Session, payload: ProjectCreate, actor: ActorContext) -> Project:
    project = Project(
        name=payload.name,
        scm_provider=payload.scm_provider,
        repo_url=str(payload.repo_url),
        default_branch=payload.default_branch,
        instructions=payload.instructions,
    )
    db.add(project)
    db.flush()

    membership = ProjectMember(project_id=project.id, user_id=actor.user_id, role="admin")
    db.add(membership)
    db.commit()
    db.refresh(project)

    write_audit(
        db=db,
        actor=actor.user_id,
        action="project.create",
        project_id=project.id,
        detail=f"created project {project.name}",
    )
    return project


def get_project(db: Session, project_id: str) -> Project | None:
    return db.query(Project).filter(Project.id == project_id).first()


def list_projects(db: Session) -> list[Project]:
    return db.query(Project).order_by(Project.created_at.desc()).all()


def update_project(db: Session, project: Project, payload: ProjectUpdate, actor: ActorContext) -> Project:
    changed_fields: list[str] = []

    if payload.name is not None:
        normalized_name = payload.name.strip()
        if not normalized_name:
            raise ValueError("name cannot be empty")
        project.name = normalized_name
        changed_fields.append("name")

    if payload.repo_url is not None:
        project.repo_url = normalize_and_validate_repo_url(project.scm_provider, payload.repo_url)
        changed_fields.append("repo_url")

    if payload.default_branch is not None:
        normalized_branch = payload.default_branch.strip()
        if project.scm_provider != "local" and not normalized_branch:
            raise ValueError("default_branch cannot be empty for github/gitlab projects")
        project.default_branch = normalized_branch
        changed_fields.append("default_branch")

    if payload.instructions is not None:
        project.instructions = payload.instructions
        changed_fields.append("instructions")

    db.commit()
    db.refresh(project)

    write_audit(
        db=db,
        actor=actor.user_id,
        action="project.update",
        project_id=project.id,
        detail=f"updated fields: {', '.join(changed_fields)}",
    )
    return project


def list_project_memories(
    db: Session,
    project_id: str,
    include_archived: bool = False,
) -> list[ProjectMemory]:
    query = db.query(ProjectMemory).filter(ProjectMemory.project_id == project_id)
    if not include_archived:
        query = query.filter(ProjectMemory.archived.is_(False))
    return query.order_by(ProjectMemory.updated_at.desc(), ProjectMemory.created_at.desc()).all()


def get_project_memory(db: Session, project_id: str, memory_id: str) -> ProjectMemory | None:
    return (
        db.query(ProjectMemory)
        .filter(ProjectMemory.project_id == project_id, ProjectMemory.id == memory_id)
        .first()
    )


def create_project_memory(
    db: Session,
    project_id: str,
    content: str,
    actor: ActorContext,
) -> ProjectMemory:
    now = datetime.utcnow()
    row = ProjectMemory(
        project_id=project_id,
        content=content,
        created_by=actor.user_id,
        archived=False,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    write_audit(
        db=db,
        actor=actor.user_id,
        action="project.memory.create",
        project_id=project_id,
        detail=f"memory={row.id}",
    )
    return row


def update_project_memory(
    db: Session,
    row: ProjectMemory,
    content: str | None,
    archived: bool | None,
    actor: ActorContext,
) -> ProjectMemory:
    if content is not None:
        row.content = content
    if archived is not None:
        row.archived = archived
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)

    write_audit(
        db=db,
        actor=actor.user_id,
        action="project.memory.update",
        project_id=row.project_id,
        detail=f"memory={row.id}",
    )
    return row
