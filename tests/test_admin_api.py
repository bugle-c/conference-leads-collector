import time
import asyncio
import threading
from pathlib import Path

import httpx
import jwt
import pytest

from conference_leads_collector.config import AppSettings
from conference_leads_collector.storage.db import create_engine, create_schema
from conference_leads_collector.services.worker import _render_conference_pages
from conference_leads_collector.web.app import create_app


class StubFetcher:
    def fetch(self, url: str):
        return 200, """
        <section>
          <h2>Speakers</h2>
          <div class="speaker-card"><h3>Jane Smith</h3><p>CMO, Example Co</p></div>
        </section>
        """


def build_settings() -> AppSettings:
    return AppSettings(
        app_env="test",
        admin_jwt_secret="super-secret-token-with-32-bytes",
        database_url="sqlite+pysqlite:///unused.db",
        redis_url="redis://localhost:6379/0",
    )


def build_token(settings: AppSettings) -> str:
    return jwt.encode(
        {"sub": "admin", "exp": int(time.time()) + 3600},
        settings.admin_jwt_secret,
        algorithm="HS256",
    )


@pytest.mark.anyio
async def test_import_sources_and_run_worker_once(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)
    settings = build_settings()
    app = create_app(settings, engine=engine, fetcher=StubFetcher())
    transport = httpx.ASGITransport(app=app)
    token = build_token(settings)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        import_response = await client.post(
            "/api/sources/import",
            json={"urls": ["https://example.com/conf"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert import_response.status_code == 200
        assert import_response.json() == {"inserted": 1, "skipped": 0}

        run_response = await client.post(
            "/api/jobs/run-once",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert run_response.status_code == 200
        assert run_response.json() == {"processed": True}

        dashboard_response = await client.get("/", headers={"Authorization": f"Bearer {token}"})
        assert dashboard_response.status_code == 200
        assert "Импортировано 1 новых конференций" in dashboard_response.text
        assert "Запущена обработка конференции https://example.com/conf" in dashboard_response.text
        assert "Обработка https://example.com/conf завершена: 1 спикеров, 0 спонсоров" in dashboard_response.text

        page_response = await client.get("/sources", headers={"Authorization": f"Bearer {token}"})
        assert page_response.status_code == 200
        assert "https://example.com/conf" in page_response.text
        assert "Jane Smith" in page_response.text


@pytest.mark.anyio
async def test_run_batch_processes_multiple_jobs(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)
    settings = build_settings()
    app = create_app(settings, engine=engine, fetcher=StubFetcher())
    transport = httpx.ASGITransport(app=app)
    token = build_token(settings)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/sources/import",
            json={"urls": ["https://example.com/one", "https://example.com/two"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

        batch_response = await client.post(
            "/api/jobs/run-batch",
            json={"limit": 10},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert batch_response.status_code == 200
        assert batch_response.json() == {"processed": 2, "remaining": 0}


@pytest.mark.anyio
async def test_admin_api_accepts_query_token_for_browser_actions(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)
    settings = build_settings()
    app = create_app(settings, engine=engine, fetcher=StubFetcher())
    transport = httpx.ASGITransport(app=app)
    token = build_token(settings)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        import_response = await client.post(
            f"/api/sources/import?token={token}",
            json={"urls": ["https://example.com/conf"]},
        )
        assert import_response.status_code == 200
        assert import_response.json() == {"inserted": 1, "skipped": 0}

        batch_response = await client.post(
            f"/api/jobs/run-batch?token={token}",
            json={"limit": 10},
        )
        assert batch_response.status_code == 200
        assert batch_response.json() == {"processed": 1, "remaining": 0}


@pytest.mark.anyio
async def test_operator_can_requeue_existing_conference(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)
    settings = build_settings()
    app = create_app(settings, engine=engine, fetcher=StubFetcher())
    transport = httpx.ASGITransport(app=app)
    token = build_token(settings)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        import_response = await client.post(
            "/api/sources/import",
            json={"urls": ["https://example.com/conf"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert import_response.status_code == 200

        first_run = await client.post(
            "/api/jobs/run-once",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert first_run.status_code == 200
        assert first_run.json() == {"processed": True}

        requeue_response = await client.post(
            "/api/sources/1/requeue",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert requeue_response.status_code == 200
        assert requeue_response.json()["queued"] is True
        assert requeue_response.json()["source_id"] == 1

        second_run = await client.post(
            "/api/jobs/run-once",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert second_run.status_code == 200
        assert second_run.json() == {"processed": True}

        sources_page = await client.get("/sources", headers={"Authorization": f"Bearer {token}"})
        assert sources_page.status_code == 200
        assert "Запустить заново" in sources_page.text or "Уже в очереди" in sources_page.text

        jobs_page = await client.get("/jobs", headers={"Authorization": f"Bearer {token}"})
        assert jobs_page.status_code == 200
        assert "https://example.com/conf" in jobs_page.text


@pytest.mark.anyio
async def test_render_conference_pages_runs_browser_renderer_outside_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StubRenderer:
        def render_conference(self, seed_url: str):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return [{"url": seed_url, "thread": threading.current_thread().name}]
            raise RuntimeError("browser renderer ran inside event loop")

    rendered_pages = _render_conference_pages("https://example.com/conf", renderer_factory=StubRenderer)

    assert rendered_pages == [{"url": "https://example.com/conf", "thread": rendered_pages[0]["thread"]}]
    assert rendered_pages[0]["thread"] != threading.current_thread().name


@pytest.mark.anyio
async def test_speaker_and_sponsor_exports_are_downloadable(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)
    settings = build_settings()
    app = create_app(settings, engine=engine, fetcher=StubFetcher())
    transport = httpx.ASGITransport(app=app)
    token = build_token(settings)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/sources/import",
            json={"urls": ["https://example.com/conf"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

        run_response = await client.post(
            "/api/jobs/run-once",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert run_response.status_code == 200

        speakers_page = await client.get("/speakers", headers={"Authorization": f"Bearer {token}"})
        assert speakers_page.status_code == 200
        assert "Таблица спикеров" in speakers_page.text
        assert "Jane" in speakers_page.text
        assert "Smith" in speakers_page.text

        speakers_export = await client.get("/exports/speakers.csv", headers={"Authorization": f"Bearer {token}"})
        assert speakers_export.status_code == 200
        assert "attachment; filename=\"speakers.csv\"" == speakers_export.headers["content-disposition"]
        assert "Jane Smith" in speakers_export.text

        sponsors_export = await client.get("/exports/sponsors.csv", headers={"Authorization": f"Bearer {token}"})
        assert sponsors_export.status_code == 200
        assert "attachment; filename=\"sponsors.csv\"" == sponsors_export.headers["content-disposition"]

        speakers_xlsx = await client.get("/exports/speakers.xlsx", headers={"Authorization": f"Bearer {token}"})
        assert speakers_xlsx.status_code == 200
        assert "attachment; filename=\"speakers.xlsx\"" == speakers_xlsx.headers["content-disposition"]
        assert speakers_xlsx.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

        sponsors_xlsx = await client.get("/exports/sponsors.xlsx", headers={"Authorization": f"Bearer {token}"})
        assert sponsors_xlsx.status_code == 200
        assert "attachment; filename=\"sponsors.xlsx\"" == sponsors_xlsx.headers["content-disposition"]
        assert sponsors_xlsx.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
