"""Thin HTTP adapter over the shared application composition.

Long-running workflows own their providers and event loop in a worker thread.
Transport job state is ephemeral; search/attempt/coverage history remains durable.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from importlib.resources import files
import logging
from pathlib import Path
import secrets
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from research_engine.infrastructure.bootstrap import create_application_services
from research_engine.domain.search_expression import SearchExpressionError
from research_engine.application.publication_links import publication_link


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)


class QueryInput(BaseModel):
    expression: str = Field(min_length=1, max_length=5000)
    providers: list[str] = Field(min_length=1, max_length=4)


class ScheduleInput(BaseModel):
    enabled: bool = True
    interval_minutes: int = Field(default=60, ge=1, le=43200)


class SearchInput(BaseModel):
    max_pages: int = Field(default=1, ge=1, le=100)


class SheetInput(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    enabled: bool = True


def create_app(*, data_directory: Path | None = None, service_factory=None,
               monitor: bool = False, poll_seconds: float = 60) -> FastAPI:
    if poll_seconds < 1:
        raise ValueError("poll_seconds must be at least one")
    factory = service_factory or (lambda: create_application_services(data_directory))
    token = secrets.token_urlsafe(32)
    jobs: list[dict] = []
    active: asyncio.Task | None = None
    stopping = False
    log = logging.getLogger(__name__)

    async def search(query_id: UUID, max_pages: int):
        services = app.state.services
        query = services.projects.get_query(query_id)
        if query is None:
            raise KeyError("query")
        preferences = services.settings.get()
        sources = query.enabled_providers or preferences.discovery_sources
        # Disabled sources still go through run_query to preserve rejection audit.
        service, providers = services.search_service(allowed_sources=preferences.discovery_sources)
        try:
            results = await service.run_sources(str(query_id), sources, max_pages=max_pages)
            return [{"provider": source, "result": jsonable_encoder(result),
                     "error_code": error.split(":", 1)[0] if error else None}
                    for source, result, error in results]
        finally:
            for provider in providers:
                await provider.aclose()

    def submit(kind: str, operation) -> dict:
        nonlocal active
        if stopping or (active is not None and not active.done()):
            raise HTTPException(409, "A search or monitoring job is already running")
        job = {"id": str(uuid4()), "kind": kind, "state": "running", "result": None,
               "error_code": None}
        jobs.append(job)
        del jobs[:-20]

        async def work():
            try:
                job["result"] = await asyncio.to_thread(lambda: asyncio.run(operation()))
                job["state"] = "completed"
            except Exception as exc:
                job["state"] = "failed"
                job["error_code"] = getattr(exc, "error_code", type(exc).__name__)
                log.warning("web_job_failed job_id=%s error_code=%s", job["id"], job["error_code"])

        active = asyncio.create_task(work())
        return job.copy()

    @asynccontextmanager
    async def lifespan(application):
        nonlocal stopping
        stopping = False
        application.state.services = await asyncio.to_thread(factory)
        stop = asyncio.Event()

        async def poll():
            while not stop.is_set():
                if active is None or active.done():
                    submit("monitoring", application.state.services.monitoring.run_due_once)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
                except asyncio.TimeoutError:
                    pass

        polling = asyncio.create_task(poll()) if monitor else None
        try:
            yield
        finally:
            stopping = True
            stop.set()
            if polling:
                await polling
            # Never dispose DB/provider resources under a still-running worker.
            if active:
                await asyncio.shield(active)
            await asyncio.to_thread(application.state.services.close)

    app = FastAPI(title="Research Engine — local client", lifespan=lifespan,
                  docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])

    @app.middleware("http")
    async def local_browser_guard(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            supplied = request.headers.get("x-research-token", "")
            origin = request.headers.get("origin")
            if not secrets.compare_digest(supplied, token) or (
                origin is not None and origin != str(request.base_url).rstrip("/")
            ):
                return JSONResponse({"detail": "Invalid local browser token/origin"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": "Invalid request or workflow state"}, status_code=400)

    @app.exception_handler(SearchExpressionError)
    async def invalid_expression(request, exc):
        return JSONResponse({"detail": {"message": str(exc), "position": exc.position}}, status_code=422)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "Record not found"}, status_code=404)

    @app.get("/", response_class=HTMLResponse)
    def index():
        assets = files("research_engine.web")
        guide = assets.joinpath("guide.html").read_text(encoding="utf-8")
        return (
            assets.joinpath("index.html").read_text(encoding="utf-8")
            .replace("__USER_GUIDE__", guide)
            .replace("__TOKEN__", token)
        )

    @app.get("/api/status")
    def status():
        preferences = app.state.services.settings.get()
        return {"monitoring_enabled": monitor, "poll_seconds": poll_seconds,
                "available_sources": [s.name for s in app.state.services.settings.sources()],
                "source_labels": {s.name: s.label for s in app.state.services.settings.sources()},
                "discovery_sources": preferences.discovery_sources,
                "monitoring_sources": preferences.monitoring_sources,
                "default_max_pages": preferences.max_pages}

    @app.get("/api/jobs")
    async def list_jobs():
        return jsonable_encoder(jobs[::-1])

    @app.get("/api/projects")
    def projects():
        return jsonable_encoder(app.state.services.projects.list_projects())

    @app.post("/api/projects", status_code=201)
    def create_project(body: ProjectInput):
        return jsonable_encoder(app.state.services.projects.create_project(body.name, body.description))

    def require_project(project_id):
        if app.state.services.projects.get_project(project_id) is None:
            raise KeyError("project")

    def require_query(query_id):
        if app.state.services.projects.get_query(query_id) is None:
            raise KeyError("query")

    @app.get("/api/projects/{project_id}/queries")
    def queries(project_id: UUID):
        require_project(project_id)
        return jsonable_encoder(app.state.services.projects.list_queries(project_id))

    @app.post("/api/projects/{project_id}/queries", status_code=201)
    def create_query(project_id: UUID, body: QueryInput):
        require_project(project_id)
        known = {s.name for s in app.state.services.settings.sources()}
        if len(set(body.providers)) != len(body.providers) or set(body.providers) - known:
            raise ValueError("invalid sources")
        return jsonable_encoder(app.state.services.projects.create_query(
            project_id, body.expression, enabled_providers=tuple(body.providers)))

    @app.post("/api/queries/preview")
    async def preview(body: QueryInput):
        known = {s.name for s in app.state.services.settings.sources()}
        if len(set(body.providers)) != len(body.providers) or set(body.providers) - known:
            raise ValueError("invalid sources")

        async def operation():
            service, providers = app.state.services.query_preview_service()
            try:
                return service.preview(body.expression, tuple(body.providers),
                    allowed_sources=app.state.services.settings.get().discovery_sources)
            finally:
                for provider in providers:
                    await provider.aclose()
        return jsonable_encoder(await asyncio.to_thread(lambda: asyncio.run(operation())))

    @app.put("/api/queries/{query_id}")
    def update_query(query_id: UUID, body: QueryInput):
        require_query(query_id)
        known = {s.name for s in app.state.services.settings.sources()}
        if len(set(body.providers)) != len(body.providers) or set(body.providers) - known:
            raise ValueError("invalid sources")
        return jsonable_encoder(app.state.services.projects.update_query(
            query_id, expression=body.expression, enabled_providers=tuple(body.providers)))

    @app.get("/api/projects/{project_id}/papers")
    def papers(project_id: UUID):
        require_project(project_id)
        return jsonable_encoder(app.state.services.projects.list_papers(project_id, include_excluded=True))

    @app.get("/api/projects/{project_id}/sheet")
    def get_sheet(project_id: UUID):
        require_project(project_id)
        return jsonable_encoder(app.state.services.sheets.get(project_id))

    @app.put("/api/projects/{project_id}/sheet")
    def configure_sheet(project_id: UUID, body: SheetInput):
        require_project(project_id)
        return jsonable_encoder(app.state.services.sheets.configure(project_id, body.url, body.enabled))

    @app.post("/api/projects/{project_id}/sheet/sync", status_code=202)
    async def sync_sheet(project_id: UUID):
        await asyncio.to_thread(require_project, project_id)
        async def operation():
            return jsonable_encoder(app.state.services.sheets.sync(project_id))
        return submit("sheet_sync", operation)

    @app.get("/api/projects/{project_id}/papers/{paper_id}")
    def detail(project_id: UUID, paper_id: UUID):
        value = app.state.services.projects.get_paper_detail(project_id, paper_id)
        if value is None:
            raise KeyError("paper")
        payload = jsonable_encoder(value)
        payload["publication_url"] = publication_link(value)
        return payload

    @app.get("/api/queries/{query_id}/history")
    def history(query_id: UUID):
        require_query(query_id)
        services = app.state.services
        return jsonable_encoder({"runs": services.projects.list_query_runs(query_id),
                                 "attempts": services.recovery.list_attempts(str(query_id)),
                                 "scheduled_executions": services.monitoring.list_executions(str(query_id)),
                                 "coverage": services.recovery.list_coverage(str(query_id)),
                                 "schedule": services.monitoring.get_schedule(str(query_id))})

    @app.post("/api/queries/{query_id}/search", status_code=202)
    async def run_search(query_id: UUID, body: SearchInput):
        await asyncio.to_thread(require_query, query_id)
        return submit("search", lambda: search(query_id, body.max_pages))

    @app.put("/api/queries/{query_id}/schedule")
    def schedule(query_id: UUID, body: ScheduleInput):
        require_query(query_id)
        return jsonable_encoder(app.state.services.monitoring.configure(
            str(query_id), interval_minutes=body.interval_minutes, enabled=body.enabled))

    @app.post("/api/monitoring/run-due", status_code=202)
    async def due():
        return submit("monitoring", app.state.services.monitoring.run_due_once)

    return app
