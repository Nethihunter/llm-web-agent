"""FastAPI application: JSON API + HTML dashboard.

A single FastAPI app hosts two surfaces:

- ``/api/*``        JSON endpoints, consumed by external integrations
                    and the future OpenAPI clients. Stateless.
- ``/dashboard/*``  HTML pages (Jinja2 + HTMX), consumed by a human in a
                    browser. Stateful only through the workflow cache.

The ``/health`` endpoint is used by ``docker-compose.yml`` to gate the
``web`` container on a real dependency check (the workflow database),
not just on "the process is up".

Architecture note:
    Mount order matters. ``/static`` is mounted **before** the routers so
    that ``StaticFiles`` wins over any router path that would otherwise
    match ``/static/*``. ``/`` is declared **after** the routers so that
    ``/api`` and ``/dashboard`` are never shadowed by the redirect.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from dashboard.routes import api_router, dashboard_router
from workflow_manager import WorkflowManager

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
DASHBOARD_DIR = BASE_DIR / "dashboard"
STATIC_DIR = DASHBOARD_DIR / "static"

# StaticFiles raises at mount time if the directory is missing. The
# directory is created by scripts/install.sh (via fetch_dashboard_assets.sh);
# if we reach this point without it, the install is incomplete. We warn
# instead of failing so the JSON API stays usable on a fresh checkout.
if not STATIC_DIR.exists():
    logger.warning(
        "Dashboard static directory not found: %s. "
        "Run scripts/fetch_dashboard_assets.sh to populate it. "
        "The JSON API will work, the dashboard HTML will not.",
        STATIC_DIR,
    )
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="LLM Web Agent",
    description="LLM-driven browser automation with workflow caching",
    version="1.0.0",
)

# Mounted first: StaticFiles must win over routers that could match /static/*.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(api_router, prefix="/api", tags=["api"])
app.include_router(dashboard_router, prefix="/dashboard", tags=["dashboard"])


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """Send the bare root to the dashboard.

    ``include_in_schema=False`` keeps this out of the OpenAPI docs: it is
    a convenience redirect for humans, not an API contract.
    """
    return RedirectResponse("/dashboard/")


@app.get("/health")
def health() -> dict:
    """Liveness + database check, used by the Docker healthcheck.

    Returns ``{"status": "ok"}`` only if the workflow database answers a
    trivial query. Any exception becomes a 500, which makes the container
    report ``unhealthy`` — that is what we want: a web process that cannot
    persist workflows is not actually ready to serve requests.
    """
    # Import here to avoid a hard dependency for users who only want the
    # JSON API without installing the full stack. WorkflowManager() also
    # initializes the schema on first call, which is the real check.
    WorkflowManager().get_stats()
    return {"status": "ok"}
