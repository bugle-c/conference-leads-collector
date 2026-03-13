from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request
import jwt
from jwt import InvalidTokenError

from conference_leads_collector.config import AppSettings
from conference_leads_collector.services.worker import process_next_job
from conference_leads_collector.services.tenchat import discover_tenchat_profiles
from conference_leads_collector.storage.db import create_engine, create_schema, session_scope
from conference_leads_collector.storage.repositories import ConferenceSourceRepository, JobRepository, TenchatProfileRepository


def _require_token(authorization: str | None, settings: AppSettings, query_token: str | None = None) -> dict:
    token_value = None
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token:
            token_value = token
    elif query_token:
        token_value = query_token

    if not token_value:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    try:
        return jwt.decode(token_value, settings.admin_jwt_secret, algorithms=["HS256"])
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid bearer token") from exc


def create_app(settings: AppSettings, engine=None, fetcher=None) -> FastAPI:
    app = FastAPI(title="Conference Leads Collector")
    app.state.engine = engine or create_engine(settings.database_url)
    app.state.fetcher = fetcher
    create_schema(app.state.engine)
    package_templates_dir = Path(__file__).parent / "templates"
    source_templates_dir = Path("/app/src/conference_leads_collector/web/templates")
    templates_dir = package_templates_dir if package_templates_dir.exists() else source_templates_dir
    templates = Jinja2Templates(directory=str(templates_dir))

    def _load_dashboard_context() -> dict:
        with session_scope(app.state.engine) as session:
            sources = ConferenceSourceRepository(session).list_sources()
            jobs = JobRepository(session).list_jobs()
            tenchat_profiles = TenchatProfileRepository(session).list_profiles()
            return {
                "sources": sources,
                "jobs": jobs,
                "tenchat_profiles": tenchat_profiles,
                "sources_count": len(sources),
                "jobs_count": len(jobs),
                "speakers_count": sum(len(source.speakers) for source in sources),
                "sponsors_count": sum(len(source.sponsors) for source in sources),
                "tenchat_count": len(tenchat_profiles),
            }

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "app_env": settings.app_env}

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request, authorization: str | None = Header(default=None), token: str | None = None):
        _require_token(authorization, settings, query_token=token)
        context = _load_dashboard_context()
        return templates.TemplateResponse(request, "dashboard.html", context)

    @app.get("/sources", response_class=HTMLResponse)
    async def sources_page(request: Request, authorization: str | None = Header(default=None), token: str | None = None):
        _require_token(authorization, settings, query_token=token)
        context = _load_dashboard_context()
        return templates.TemplateResponse(request, "sources.html", context)

    @app.get("/jobs", response_class=HTMLResponse)
    async def jobs_page(request: Request, authorization: str | None = Header(default=None), token: str | None = None):
        _require_token(authorization, settings, query_token=token)
        context = _load_dashboard_context()
        return templates.TemplateResponse(request, "jobs.html", context)

    @app.get("/tenchat", response_class=HTMLResponse)
    async def tenchat_page(request: Request, authorization: str | None = Header(default=None), token: str | None = None):
        _require_token(authorization, settings, query_token=token)
        context = _load_dashboard_context()
        return templates.TemplateResponse(request, "tenchat.html", context)

    @app.post("/api/sources/import")
    async def import_sources(payload: dict, authorization: str | None = Header(default=None)) -> dict[str, int]:
        _require_token(authorization, settings)
        urls = payload.get("urls", [])
        with session_scope(app.state.engine) as session:
            sources_repo = ConferenceSourceRepository(session)
            jobs_repo = JobRepository(session)
            result = sources_repo.import_seed_urls(urls)
            for source in sources_repo.list_sources_by_urls(urls):
                if source.status == "pending":
                    jobs_repo.enqueue_crawl(source.id)
        return result

    @app.post("/api/jobs/run-once")
    async def run_once(authorization: str | None = Header(default=None)) -> dict[str, bool]:
        _require_token(authorization, settings)
        processed = process_next_job(app.state.engine, fetcher=app.state.fetcher)
        return {"processed": processed}

    @app.post("/api/tenchat/discover")
    async def discover_tenchat(payload: dict, authorization: str | None = Header(default=None)) -> dict[str, int]:
        _require_token(authorization, settings)
        queries = payload.get("queries", [])
        profiles_found = discover_tenchat_profiles(app.state.engine, queries, fetcher=app.state.fetcher)
        return {"profiles_found": profiles_found}

    return app
