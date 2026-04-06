from fastapi import APIRouter

from app.api.routes.analysis import router as analysis_router
from app.api.routes.chat import router as chat_router
from app.api.routes.health import router as health_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.knowledge import router as knowledge_router
from app.api.routes.projects import router as projects_router
from app.api.routes.query import router as query_router
from app.api.routes.symbols import router as symbols_router
from app.api.routes.web import router as web_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(projects_router)
api_router.include_router(jobs_router)
api_router.include_router(query_router)
api_router.include_router(chat_router)
api_router.include_router(analysis_router)
api_router.include_router(symbols_router)
api_router.include_router(knowledge_router)
api_router.include_router(web_router)
