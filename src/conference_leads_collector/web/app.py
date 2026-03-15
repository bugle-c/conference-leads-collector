from __future__ import annotations

import csv
import io
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from queue import Queue
from threading import Thread

import anyio
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi import Request
import jwt
from jwt import InvalidTokenError
import xlsxwriter

from conference_leads_collector.config import AppSettings
from conference_leads_collector.services.ai_gateway import fetch_ai_gateway_credits
from conference_leads_collector.services.source_import import expand_seed_urls
from conference_leads_collector.services.worker import HttpFetcher, process_next_job
from conference_leads_collector.services.tenchat import discover_tenchat_profiles
from conference_leads_collector.storage.db import create_engine, create_schema, session_scope
from conference_leads_collector.storage.repositories import (
    ActivityEventRepository,
    ConferenceSourceRepository,
    JobRepository,
    TenchatProfileRepository,
)


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


def create_app(settings: AppSettings, engine=None, fetcher=None, ai_credits_provider=None) -> FastAPI:
    app = FastAPI(title="Conference Leads Collector")
    app.state.engine = engine or create_engine(settings.database_url)
    app.state.fetcher = fetcher or HttpFetcher()
    app.state.ai_credits_provider = ai_credits_provider or fetch_ai_gateway_credits
    create_schema(app.state.engine)
    package_templates_dir = Path(__file__).parent / "templates"
    source_templates_dir = Path("/app/src/conference_leads_collector/web/templates")
    templates_dir = package_templates_dir if package_templates_dir.exists() else source_templates_dir
    templates = Jinja2Templates(directory=str(templates_dir))

    def _flatten_speakers(sources: list) -> list[dict]:
        rows: list[dict] = []
        for source in sources:
            for speaker in source.speakers:
                rows.append(
                    {
                        "conference": source.name or source.seed_url,
                        "conference_url": source.seed_url,
                        "full_name": speaker.full_name,
                        "first_name": speaker.first_name or "",
                        "last_name": speaker.last_name or "",
                        "title": speaker.title or "",
                        "company": speaker.company or "",
                        "regalia_raw": speaker.regalia_raw or "",
                    }
                )
        return rows

    def _flatten_sponsors(sources: list) -> list[dict]:
        rows: list[dict] = []
        for source in sources:
            for sponsor in source.sponsors:
                rows.append(
                    {
                        "conference": source.name or source.seed_url,
                        "conference_url": source.seed_url,
                        "name": sponsor.name,
                        "category": sponsor.category or "",
                        "website": sponsor.website or "",
                        "description": sponsor.description or "",
                    }
                )
        return rows

    def _csv_response(filename: str, fieldnames: list[str], rows: list[dict]) -> Response:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        return Response(
            content=buffer.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    def _xlsx_response(filename: str, sheet_name: str, headers: list[tuple[str, str]], rows: list[dict]) -> Response:
        buffer = io.BytesIO()
        workbook = xlsxwriter.Workbook(buffer, {"in_memory": True})
        worksheet = workbook.add_worksheet(sheet_name[:31] or "Sheet1")

        header_format = workbook.add_format({"bold": True, "bg_color": "#F3F3F0", "border": 1})
        text_format = workbook.add_format({"text_wrap": True, "valign": "top"})

        for column, (_, title) in enumerate(headers):
            worksheet.write(0, column, title, header_format)

        for row_index, row in enumerate(rows, start=1):
            for column, (key, _) in enumerate(headers):
                worksheet.write(row_index, column, row.get(key, ""), text_format)

        for column, (key, title) in enumerate(headers):
            max_length = max([len(str(title))] + [len(str(row.get(key, ""))) for row in rows[:200]], default=len(title))
            worksheet.set_column(column, column, min(max(max_length + 2, 16), 48))

        worksheet.freeze_panes(1, 0)
        workbook.close()
        return Response(
            content=buffer.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    def _format_dashboard_number(value: object) -> object:
        if value in (None, ""):
            return value
        if isinstance(value, (int, float)):
            decimal_value = Decimal(str(value))
            return f"{decimal_value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):f}"
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return value
            prefix = ""
            suffix = ""
            numeric = stripped
            if stripped[0] in "$€£":
                prefix = stripped[0]
                numeric = stripped[1:]
            try:
                decimal_value = Decimal(numeric)
            except InvalidOperation:
                return value
            rounded = decimal_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            return f"{prefix}{rounded:f}{suffix}"
        return value

    def _format_ai_gateway(ai_gateway: object) -> object:
        if ai_gateway is None:
            return None
        if isinstance(ai_gateway, dict):
            formatted = dict(ai_gateway)
            formatted["balance"] = _format_dashboard_number(formatted.get("balance"))
            formatted["total_used"] = _format_dashboard_number(formatted.get("total_used"))
            formatted["month_used"] = _format_dashboard_number(formatted.get("month_used"))
            return formatted
        for field in ("balance", "total_used", "month_used"):
            setattr(ai_gateway, field, _format_dashboard_number(getattr(ai_gateway, field, None)))
        return ai_gateway

    def _load_dashboard_context(token: str | None = None, current_page: str = "dashboard") -> dict:
        ai_gateway = None
        if current_page == "dashboard":
            ai_gateway = _format_ai_gateway(app.state.ai_credits_provider(settings))
        with session_scope(app.state.engine) as session:
            sources_repo = ConferenceSourceRepository(session)
            sources_repo.reconcile_statuses()
            sources = sources_repo.list_sources()
            jobs = JobRepository(session).list_jobs()
            tenchat_profiles = TenchatProfileRepository(session).list_profiles()
            activity_events = ActivityEventRepository(session).list_recent()
            queued_source_ids = {
                job.target_id
                for job in jobs
                if job.job_type == "crawl_conference" and job.status in {"pending", "running"}
            }
            pending_jobs_count = sum(1 for job in jobs if job.status == "pending")
            running_jobs_count = sum(1 for job in jobs if job.status == "running")
            last_job = jobs[-1] if jobs else None
            source_urls_by_id = {source.id: source.seed_url for source in sources}
            return {
                "page_title": "Панель сбора конференций",
                "sources": sources,
                "speaker_rows": _flatten_speakers(sources),
                "sponsor_rows": _flatten_sponsors(sources),
                "jobs": jobs,
                "tenchat_profiles": tenchat_profiles,
                "activity_events": activity_events,
                "sources_count": len(sources),
                "jobs_count": len(jobs),
                "pending_jobs_count": pending_jobs_count,
                "running_jobs_count": running_jobs_count,
                "speakers_count": sum(len(source.speakers) for source in sources),
                "sponsors_count": sum(len(source.sponsors) for source in sources),
                "tenchat_count": len(tenchat_profiles),
                "ai_gateway": ai_gateway,
                "token": token,
                "current_page": current_page,
                "queued_source_ids": queued_source_ids,
                "source_urls_by_id": source_urls_by_id,
                "last_job": last_job,
            }

    def _run_next_job_sync() -> bool:
        return process_next_job(app.state.engine, fetcher=app.state.fetcher, settings=settings)

    def _run_batch_sync(limit: int) -> tuple[int, int]:
        processed = 0
        for _ in range(limit):
            if not _run_next_job_sync():
                break
            processed += 1

        with session_scope(app.state.engine) as session:
            remaining = sum(1 for job in JobRepository(session).list_jobs() if job.status == "pending")
        return processed, remaining

    async def _run_in_worker_thread(func, *args):
        result_queue: Queue[tuple[bool, object]] = Queue(maxsize=1)

        def runner() -> None:
            try:
                result = func(*args)
            except Exception as exc:
                result_queue.put((False, exc))
                return
            result_queue.put((True, result))

        thread = Thread(target=runner, name="clc-web-worker", daemon=True)
        thread.start()
        while thread.is_alive():
            await anyio.sleep(0.05)
        success, payload = result_queue.get()
        if success:
            return payload
        raise payload

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "app_env": settings.app_env}

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request, authorization: str | None = Header(default=None), token: str | None = None):
        _require_token(authorization, settings, query_token=token)
        context = _load_dashboard_context(token=token, current_page="dashboard")
        context["request"] = request
        return templates.TemplateResponse(request, "dashboard.html", context)

    @app.get("/sources", response_class=HTMLResponse)
    async def sources_page(request: Request, authorization: str | None = Header(default=None), token: str | None = None):
        _require_token(authorization, settings, query_token=token)
        context = _load_dashboard_context(token=token, current_page="sources")
        context["request"] = request
        return templates.TemplateResponse(request, "sources.html", context)

    @app.get("/speakers", response_class=HTMLResponse)
    async def speakers_page(request: Request, authorization: str | None = Header(default=None), token: str | None = None):
        _require_token(authorization, settings, query_token=token)
        context = _load_dashboard_context(token=token, current_page="speakers")
        context["request"] = request
        return templates.TemplateResponse(request, "speakers.html", context)

    @app.get("/sponsors", response_class=HTMLResponse)
    async def sponsors_page(request: Request, authorization: str | None = Header(default=None), token: str | None = None):
        _require_token(authorization, settings, query_token=token)
        context = _load_dashboard_context(token=token, current_page="sponsors")
        context["request"] = request
        return templates.TemplateResponse(request, "sponsors.html", context)

    @app.get("/jobs", response_class=HTMLResponse)
    async def jobs_page(request: Request, authorization: str | None = Header(default=None), token: str | None = None):
        _require_token(authorization, settings, query_token=token)
        context = _load_dashboard_context(token=token, current_page="jobs")
        context["request"] = request
        return templates.TemplateResponse(request, "jobs.html", context)

    @app.get("/tenchat", response_class=HTMLResponse)
    async def tenchat_page(request: Request, authorization: str | None = Header(default=None), token: str | None = None):
        _require_token(authorization, settings, query_token=token)
        context = _load_dashboard_context(token=token, current_page="tenchat")
        context["request"] = request
        return templates.TemplateResponse(request, "tenchat.html", context)

    @app.post("/api/sources/import")
    async def import_sources(
        payload: dict,
        authorization: str | None = Header(default=None),
        token: str | None = None,
    ) -> dict[str, int]:
        _require_token(authorization, settings, query_token=token)
        urls = payload.get("urls", [])
        import_urls = expand_seed_urls(urls, app.state.fetcher)
        with session_scope(app.state.engine) as session:
            sources_repo = ConferenceSourceRepository(session)
            jobs_repo = JobRepository(session)
            events_repo = ActivityEventRepository(session)
            result = sources_repo.import_seed_urls(import_urls)
            for source in sources_repo.list_sources_by_urls(import_urls):
                if source.status == "pending":
                    jobs_repo.enqueue_crawl(source.id)
            expanded_sources = max(0, len(import_urls) - len(urls))
            details = f"Пропущено дублей: {result['skipped']}"
            if expanded_sources:
                details = f"{details}. Архив развёрнут в {expanded_sources} дополнительных конференций"
            events_repo.add_event(
                f"Импортировано {result['inserted']} новых конференций",
                details,
            )
        return result

    @app.post("/api/jobs/run-once")
    async def run_once(
        authorization: str | None = Header(default=None),
        token: str | None = None,
    ) -> dict[str, bool]:
        _require_token(authorization, settings, query_token=token)
        processed = await _run_in_worker_thread(_run_next_job_sync)
        return {"processed": processed}

    @app.post("/api/sources/{source_id}/requeue")
    async def requeue_source(
        source_id: int,
        authorization: str | None = Header(default=None),
        token: str | None = None,
    ) -> dict[str, int | bool | str]:
        _require_token(authorization, settings, query_token=token)
        with session_scope(app.state.engine) as session:
            sources_repo = ConferenceSourceRepository(session)
            jobs_repo = JobRepository(session)
            events_repo = ActivityEventRepository(session)
            source = sources_repo.get_source(source_id)
            if source is None:
                raise HTTPException(status_code=404, detail="Conference source not found")

            job = jobs_repo.enqueue_crawl(source.id, force=True, priority=0)
            sources_repo.mark_pending(source.id)
            source_url = source.seed_url
            job_id = job.id
            events_repo.add_event(
                f"Конференция поставлена в очередь повторно: {source.seed_url}",
                f"Задача #{job.id} готова к повторной обработке",
            )
        processed = await _run_in_worker_thread(_run_next_job_sync)
        return {
            "queued": True,
            "processed": processed,
            "source_id": source_id,
            "job_id": job_id,
            "seed_url": source_url,
        }

    @app.post("/api/jobs/run-batch")
    async def run_batch(
        payload: dict,
        authorization: str | None = Header(default=None),
        token: str | None = None,
    ) -> dict[str, int]:
        _require_token(authorization, settings, query_token=token)
        requested_limit = payload.get("limit", 10)
        try:
            limit = max(1, min(int(requested_limit), 200))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid limit") from exc

        processed, remaining = await _run_in_worker_thread(_run_batch_sync, limit)
        return {"processed": processed, "remaining": remaining}

    @app.post("/api/tenchat/discover")
    async def discover_tenchat(
        payload: dict,
        authorization: str | None = Header(default=None),
        token: str | None = None,
    ) -> dict[str, int]:
        _require_token(authorization, settings, query_token=token)
        queries = payload.get("queries", [])
        profiles_found = await _run_in_worker_thread(
            discover_tenchat_profiles,
            app.state.engine,
            queries,
            app.state.fetcher,
        )
        return {"profiles_found": profiles_found}

    @app.get("/exports/speakers.csv")
    async def export_speakers_csv(authorization: str | None = Header(default=None), token: str | None = None) -> Response:
        _require_token(authorization, settings, query_token=token)
        with session_scope(app.state.engine) as session:
            sources = ConferenceSourceRepository(session).list_sources()
        rows = _flatten_speakers(sources)
        return _csv_response(
            "speakers.csv",
            ["conference", "conference_url", "full_name", "first_name", "last_name", "title", "company", "regalia_raw"],
            rows,
        )

    @app.get("/exports/sponsors.csv")
    async def export_sponsors_csv(authorization: str | None = Header(default=None), token: str | None = None) -> Response:
        _require_token(authorization, settings, query_token=token)
        with session_scope(app.state.engine) as session:
            sources = ConferenceSourceRepository(session).list_sources()
        rows = _flatten_sponsors(sources)
        return _csv_response(
            "sponsors.csv",
            ["conference", "conference_url", "name", "category", "website", "description"],
            rows,
        )

    @app.get("/exports/speakers.xlsx")
    async def export_speakers_xlsx(authorization: str | None = Header(default=None), token: str | None = None) -> Response:
        _require_token(authorization, settings, query_token=token)
        with session_scope(app.state.engine) as session:
            sources = ConferenceSourceRepository(session).list_sources()
        rows = _flatten_speakers(sources)
        return _xlsx_response(
            "speakers.xlsx",
            "Speakers",
            [
                ("first_name", "Имя"),
                ("last_name", "Фамилия"),
                ("regalia_raw", "Регалии и должность"),
                ("company", "Компания"),
                ("conference", "Конференция"),
                ("conference_url", "URL конференции"),
            ],
            rows,
        )

    @app.get("/exports/sponsors.xlsx")
    async def export_sponsors_xlsx(authorization: str | None = Header(default=None), token: str | None = None) -> Response:
        _require_token(authorization, settings, query_token=token)
        with session_scope(app.state.engine) as session:
            sources = ConferenceSourceRepository(session).list_sources()
        rows = _flatten_sponsors(sources)
        return _xlsx_response(
            "sponsors.xlsx",
            "Sponsors",
            [
                ("name", "Название"),
                ("category", "Категория"),
                ("website", "Сайт"),
                ("description", "Описание"),
                ("conference", "Конференция"),
                ("conference_url", "URL конференции"),
            ],
            rows,
        )

    @app.get("/exports/tenchat.xlsx")
    async def export_tenchat_xlsx(authorization: str | None = Header(default=None), token: str | None = None) -> Response:
        _require_token(authorization, settings, query_token=token)
        with session_scope(app.state.engine) as session:
            profiles = TenchatProfileRepository(session).list_profiles()
        rows = [
            {
                "full_name": profile.full_name or "",
                "job_title": profile.job_title or "",
                "followers": profile.followers or "",
                "profile_url": profile.profile_url,
                "source_query": profile.source_query or "",
            }
            for profile in profiles
        ]
        return _xlsx_response(
            "tenchat.xlsx",
            "TenChat",
            [
                ("full_name", "Имя"),
                ("job_title", "Должность"),
                ("followers", "Подписчики"),
                ("profile_url", "Ссылка"),
                ("source_query", "Запрос"),
            ],
            rows,
        )

    return app
