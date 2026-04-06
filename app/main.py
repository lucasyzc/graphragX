from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import engine
from app.services.runtime_check import run_startup_checks


@asynccontextmanager
async def app_lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    settings = get_settings()
    if settings.app_env != "test":
        run_startup_checks()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=app_lifespan)
    static_dir = Path(__file__).resolve().parent / "web" / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        return Response(status_code=204)

    app.include_router(api_router)
    return app


app = create_app()
