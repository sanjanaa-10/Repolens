"""RepoLens backend entry point.

Phase 1 adds real repository ingestion: safe clone, file discovery, and
metadata storage for real public GitHub repositories.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.routes import router
from app.config import get_settings
from app.core.database import init_db
from app.core.logging import configure_logging
from app.core.security import (
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
    install_error_handlers,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(debug=get_settings().debug)
    await init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        lifespan=lifespan,
    )

    origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
    ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Local-developer default allows loopback and the in-process test host
    # ("test"); deployed installs are expected to list their real hosts via
    # REPOLENS_TRUSTED_HOSTS and proxy through a reverse proxy.
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.trusted_hosts,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIDMiddleware)

    install_error_handlers(app)

    app.include_router(router, prefix="/api", tags=["api"])

    return app


app = create_app()