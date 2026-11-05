"""DPI CRM API. Run: ``uv run uvicorn app.main:app --port 8100``."""

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.api.v1 import companies, contacts, events, jobs, status
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.linkedin import LinkedInClient
from app.workers.runner import JobRunner
from app.workers.scheduler import build_scheduler

logging.basicConfig(level=logging.INFO)


def create_app(
    session_factory: Callable[[], Session] = SessionLocal,
    client_factory: Callable[[], LinkedInClient] = LinkedInClient,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.client_factory = client_factory
        app.state.runner = JobRunner(session_factory, client_factory)
        app.state.runner.start()
        app.state.scheduler = None
        if get_settings().scheduler_enabled:
            app.state.scheduler = build_scheduler(app.state.runner)
            app.state.scheduler.start()
        yield
        if app.state.scheduler:
            app.state.scheduler.shutdown(wait=False)
        await app.state.runner.stop()

    app = FastAPI(title="DPI CRM", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for module in (status, contacts, events, companies, jobs):
        app.include_router(module.router, prefix="/api/v1")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
