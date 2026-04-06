from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import ActorContext, get_actor_context, require_project_role
from app.db.models import Symbol
from app.db.session import get_db
from app.schemas.analysis import ImpactRequest, ImpactResponse
from app.services.impact_service import estimate_impact

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.post("/impact", response_model=ImpactResponse)
def impact_endpoint(
    payload: ImpactRequest,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> ImpactResponse:
    require_project_role(db=db, project_id=payload.project_id, actor=actor, min_role="viewer")

    symbols = db.query(Symbol).filter(Symbol.project_id == payload.project_id).all()
    changed_files, impacted_symbols, notes = estimate_impact(
        project_id=payload.project_id,
        changed_files=payload.file_paths,
        symbols=symbols,
    )
    return ImpactResponse(changed_files=changed_files, impacted_symbols=impacted_symbols, notes=notes)
