from dataclasses import dataclass

from fastapi import Header, HTTPException
from sqlalchemy.orm import Session

from app.db.models import ProjectMember


@dataclass
class ActorContext:
    user_id: str
    role: str


ROLE_ORDER = {"viewer": 1, "editor": 2, "admin": 3}


def get_actor_context(x_user: str | None = Header(default=None), x_role: str | None = Header(default=None)) -> ActorContext:
    user_id = x_user or "system"
    role = x_role or "admin"
    if role not in ROLE_ORDER:
        raise HTTPException(status_code=400, detail="Invalid role. Expected viewer|editor|admin")
    return ActorContext(user_id=user_id, role=role)


def require_project_role(db: Session, project_id: str, actor: ActorContext, min_role: str) -> None:
    membership = (
        db.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == actor.user_id)
        .first()
    )
    if not membership:
        raise HTTPException(status_code=403, detail="No access to project")
    if ROLE_ORDER[membership.role] < ROLE_ORDER[min_role]:
        raise HTTPException(status_code=403, detail="Insufficient project role")
