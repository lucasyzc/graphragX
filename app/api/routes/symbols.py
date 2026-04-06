from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import ActorContext, get_actor_context, require_project_role
from app.db.models import Symbol
from app.db.session import get_db
from app.schemas.symbol import SymbolResponse

router = APIRouter(prefix="/symbols", tags=["symbols"])


@router.get("/{symbol_id}", response_model=SymbolResponse)
def get_symbol(
    symbol_id: str,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> SymbolResponse:
    symbol = db.query(Symbol).filter(Symbol.id == symbol_id).first()
    if not symbol:
        raise HTTPException(status_code=404, detail="Symbol not found")

    require_project_role(db=db, project_id=symbol.project_id, actor=actor, min_role="viewer")
    return SymbolResponse.model_validate(symbol)
