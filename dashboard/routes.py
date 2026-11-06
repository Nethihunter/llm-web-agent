"""Dashboard and API routes."""
from __future__ import annotations

import logging
import threading
from html import escape
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from agent import WebAgent
from dashboard.helpers import (
    missing_variables,
    normalize_domain,
    task_variables,
    valid_domain,
)
from workflow_manager import WorkflowManager

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")
wf_manager = WorkflowManager()

api_router = APIRouter()
dashboard_router = APIRouter()

# One browser, one task at a time.
_run_lock = threading.Lock()


# Routes use plain ``def`` (not ``async def``): the database drivers are
# blocking, FastAPI runs plain functions in a thread pool.

def _require_htmx(request: Request) -> None:
    """Reject requests that do not come from our own htmx pages.

    A web page on another site can submit a cross-site form to
    http://localhost:8000/... but cannot add a custom header without a CORS
    preflight, so this header check blocks that CSRF.
    """
    if request.headers.get("HX-Request") != "true":
        raise HTTPException(403, "HX-Request header required")


def _load_or_404(domain: str, name: str):
    if not valid_domain(domain):
        raise HTTPException(404, "Workflow not found")
    wf = wf_manager.load_workflow(f"https://{domain}", name)
    if not wf:
        raise HTTPException(404, "Workflow not found")
    return wf


# ---------------------------------------------------------------- JSON API
@api_router.get("/workflows")
def api_list_workflows(domain: str | None = None):
    return wf_manager.list_workflows(domain)


@api_router.get("/workflows/{domain}/{name}")
def api_get_workflow(domain: str, name: str):
    wf = _load_or_404(domain, name)
    return {
        "id": wf.id, "domain": wf.domain, "name": wf.name,
        "goal": wf.goal, "success_indicator": wf.success_indicator,
        "steps": [
            {"order": s.order, "action": s.action, "selector": s.selector,
             "value_template": s.value_template}
            for s in wf.steps
        ],
    }


@api_router.get("/runs")
def api_list_runs(limit: int = 50, domain: str | None = None):
    return wf_manager.list_runs(limit=limit, domain=domain)


@api_router.delete("/workflows/{domain}/{name}")
def api_delete_workflow(domain: str, name: str):
    if not valid_domain(domain) or not wf_manager.delete_workflow(f"https://{domain}", name):
        raise HTTPException(404, "Workflow not found")
    return {"deleted": True}


# ---------------------------------------------------------------- HTML pages
@dashboard_router.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "stats": wf_manager.get_stats(),
            "runs": wf_manager.list_runs(limit=10),
        },
    )


@dashboard_router.get("/workflows", response_class=HTMLResponse)
def workflows_page(request: Request, domain: str | None = None):
    return templates.TemplateResponse(
        request=request,
        name="workflows.html",
        context={"workflows": wf_manager.list_workflows(domain), "domain": domain},
    )


@dashboard_router.get("/workflows/{domain}/{name}", response_class=HTMLResponse)
def workflow_detail(request: Request, domain: str, name: str):
    wf = _load_or_404(domain, name)
    return templates.TemplateResponse(
        request=request,
        name="workflow_detail.html",
        context={"workflow": wf, "domain": domain},
    )


@dashboard_router.get("/runs", response_class=HTMLResponse)
def runs_page(request: Request, limit: int = 50):
    return templates.TemplateResponse(
        request=request,
        name="runs.html",
        context={"runs": wf_manager.list_runs(limit=limit)},
    )


# ---------------------------------------------------------------- actions
@dashboard_router.post("/workflows/{domain}/{name}/run")
def run_workflow(request: Request, domain: str, name: str,
                 background: BackgroundTasks):
    _require_htmx(request)
    wf = _load_or_404(domain, name)

    # The dashboard has no form for variables: use the demo task's defaults.
    # A missing variable must never be typed literally as "{password}".
    variables = task_variables().get((normalize_domain(domain), name), {})
    missing = missing_variables(wf, variables)
    if missing:
        return HTMLResponse(
            '<span class="text-red-600">✗ Missing variables: '
            f"{escape(', '.join(missing))} (run it from the CLI with --var)</span>"
        )

    if not _run_lock.acquire(blocking=False):
        return HTMLResponse('<span class="text-muted">⏳ A run is already in progress</span>')

    def _run() -> None:
        try:
            agent = WebAgent(auto_confirm=True, browser_mode="remote",
                             use_workflow_cache=True)
            agent.execute_task(
                url=f"https://{domain}",
                workflow_name=name,
                variables=variables,
                save_on_success=False,
            )
        except Exception:
            logger.exception("Background run failed (%s/%s)", domain, name)
        finally:
            _run_lock.release()

    background.add_task(_run)
    return HTMLResponse('<span class="text-green-600">✓ Run started</span>')


@dashboard_router.delete("/workflows/{domain}/{name}")
def delete_workflow(request: Request, domain: str, name: str):
    _require_htmx(request)
    if not valid_domain(domain) or not wf_manager.delete_workflow(f"https://{domain}", name):
        return HTMLResponse('<span class="text-red-600">✗ Not found</span>')
    return HTMLResponse("")


# ---------------------------------------------------------------- chart data
@dashboard_router.get("/api/chart/runs")
def chart_runs():
    return JSONResponse(wf_manager.get_recent_runs_for_chart(hours=24))


@dashboard_router.get("/api/chart/tokens")
def chart_tokens():
    return JSONResponse(wf_manager.get_token_usage_for_chart(hours=24))
