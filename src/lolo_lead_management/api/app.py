from __future__ import annotations

from fastapi import FastAPI

from lolo_lead_management.application.container import build_container
from lolo_lead_management.config.settings import Settings, get_settings

from .routes.health import router as health_router
from .routes.query_memory import router as memory_router
from .routes.runs import router as runs_router
from .routes.shortlists import router as shortlists_router


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    app = FastAPI(
        title=app_settings.app_name,
        docs_url="/",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.state.container = build_container(app_settings)
    app.include_router(health_router)
    app.include_router(runs_router)
    app.include_router(shortlists_router)
    app.include_router(memory_router)
    return app


app = create_app()
