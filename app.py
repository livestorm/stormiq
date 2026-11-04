"""StormIQ web entry point (`uvicorn app:app`).

Creates the FastAPI app and wires it together. The routes live in
livestorm_app/routes/, one module per area; docs/api.md lists them.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from livestorm_app.auth import LOCAL_DEV_ENV_VAR, local_dev_enabled, running_on_render
from livestorm_app.config import load_env_file
from livestorm_app.db import close_db_pool, ensure_database_schema
from livestorm_app.queue import close_queue_bridge
from livestorm_app.routes import admin, ai, oauth, sessions, spa, system, workspace
from livestorm_app.timing import RequestTimingMiddleware


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BRAND_ASSETS_DIR = Path(__file__).resolve().parent / "assets"


load_env_file()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    try:
        ensure_database_schema()
    except Exception:
        logger.exception("Database schema initialization failed")
    if local_dev_enabled():
        logger.warning(
            "Local-dev mode is ON (%s): requests without an OAuth session act as a trusted local operator.",
            LOCAL_DEV_ENV_VAR,
        )
    yield
    close_queue_bridge()
    close_db_pool()


# Interactive API docs and the OpenAPI schema are a local convenience; in
# production they would publish the full route map to anonymous visitors.
_API_DOCS_ENABLED = not running_on_render()

app = FastAPI(
    title="Livestorm Post Event Analysis API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if _API_DOCS_ENABLED else None,
    redoc_url="/redoc" if _API_DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _API_DOCS_ENABLED else None,
)

# No CORS middleware: the SPA is served by this app (and proxied by Vite in
# dev), so every browser request is same-origin.

# One `timing ...` log line per API request (route template, status, ms, bytes).
app.add_middleware(RequestTimingMiddleware)

if BRAND_ASSETS_DIR.exists():
    app.mount("/brand-assets", StaticFiles(directory=BRAND_ASSETS_DIR), name="brand-assets")

# Routes match in registration order. Every new session route must take its
# identity from livestorm_app.auth (tests/test_access_control.py checks it).
for routes in (system, admin, oauth, workspace, sessions, ai):
    app.include_router(routes.router)

if (spa.FRONTEND_DIST_DIR / "assets").is_dir():
    app.mount("/assets", spa.ImmutableStaticFiles(directory=spa.FRONTEND_DIST_DIR / "assets"), name="assets")

# The SPA catch-all (`GET /{full_path:path}`) must stay last.
app.include_router(spa.router)
