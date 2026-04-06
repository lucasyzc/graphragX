from sqlalchemy.orm import Session

from app.db.models import AuditLog


def write_audit(db: Session, actor: str, action: str, project_id: str | None = None, detail: str | None = None) -> None:
    row = AuditLog(actor=actor, action=action, project_id=project_id, detail=detail)
    db.add(row)
    db.commit()
