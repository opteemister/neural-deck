"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.router import router
from app.core.config import get_settings
from app.db.session import dispose_engine

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Prepare runtime directories and release database resources on shutdown."""

    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.model_engines_dir.mkdir(parents=True, exist_ok=True)
    settings.model_log_dir.mkdir(parents=True, exist_ok=True)
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    """Build the FastAPI application."""

    application = FastAPI(title="LLM Studio", version="0.1.0", lifespan=lifespan)
    application.include_router(router)
    if FRONTEND_DIST.is_dir():
        application.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
    return application


app = create_app()
