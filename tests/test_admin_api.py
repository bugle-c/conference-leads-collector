import time
import asyncio
import threading
from pathlib import Path

import httpx
import jwt
import pytest
from sqlalchemy.exc import OperationalError

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


class ArchiveIndexFetcher:
    def fetch(self, url: str):
        if url == "https://aij.ru":
            return 200, """
            <html>
              <body>
                <a href="/program/2024">AIJ 2024</a>
                <a href="/program/2025">AIJ 2025</a>
                <a href="/contacts">Contacts</a>
              </body>
            </html>
            """
        if url == "https://aij.ru/sitemap.xml":
            return 200, """
            <urlset>
              <url><loc>https://aij.ru/program/2024</loc></url>
              <url><loc>https://aij.ru/program/2025</loc></url>
              <url><loc>https://aij.ru/program/2023</loc></url>
            </urlset>
            """
        return 404, ""


class NestedArchiveFetcher:
    def fetch(self, url: str):
        if url == "https://archive.example.com":
            return 200, """
            <html>
              <body>
                <a href="/archive">Archive</a>
                <a href="/program">Program</a>
              </body>
            </html>
            """
        if url == "https://archive.example.com/sitemap.xml":
            return 200, """
            <urlset>
              <url><loc>https://archive.example.com/archive</loc></url>
              <url><loc>https://archive.example.com/program</loc></url>
            </urlset>
            """
        if url == "https://archive.example.com/archive":
            return 200, """
            <html>
              <body>
                <script>
                  window.__DATA__ = {
                    "items": ["/events/forum-2023", "/events/forum-2024", "/events/forum-2025"]
                  };
                </script>
              </body>
            </html>
            """
        if url == "https://archive.example.com/program":
            return 200, """
            <html>
              <body>
                <a href="/events/forum-2024">Forum 2024</a>
              </body>
            </html>
            """
        return 404, ""


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
async def test_import_sources_expands_archive_index_into_conference_urls(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)
    settings = build_settings()
    app = create_app(settings, engine=engine, fetcher=ArchiveIndexFetcher())
    transport = httpx.ASGITransport(app=app)
    token = build_token(settings)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        import_response = await client.post(
            "/api/sources/import",
            json={"urls": ["https://aij.ru"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert import_response.status_code == 200
        assert import_response.json() == {"inserted": 3, "skipped": 0}

        sources_page = await client.get("/sources", headers={"Authorization": f"Bearer {token}"})
        assert sources_page.status_code == 200
        assert "https://aij.ru/program/2023" in sources_page.text
        assert "https://aij.ru/program/2024" in sources_page.text
        assert "https://aij.ru/program/2025" in sources_page.text
        assert 'data-seed-url="https://aij.ru"' not in sources_page.text

        jobs_page = await client.get("/jobs", headers={"Authorization": f"Bearer {token}"})
        assert jobs_page.status_code == 200
        assert "https://aij.ru/program/2023" in jobs_page.text
        assert "https://aij.ru/program/2024" in jobs_page.text
        assert "https://aij.ru/program/2025" in jobs_page.text


@pytest.mark.anyio
async def test_import_sources_expands_archive_index_with_default_web_fetcher(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)
    settings = build_settings()
    monkeypatch.setattr("conference_leads_collector.web.app.HttpFetcher", ArchiveIndexFetcher)
    app = create_app(settings, engine=engine)
    transport = httpx.ASGITransport(app=app)
    token = build_token(settings)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        import_response = await client.post(
            "/api/sources/import",
            json={"urls": ["https://aij.ru"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert import_response.status_code == 200
        assert import_response.json() == {"inserted": 3, "skipped": 0}


@pytest.mark.anyio
async def test_import_sources_expands_nested_archive_hubs_into_conference_urls(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)
    settings = build_settings()
    app = create_app(settings, engine=engine, fetcher=NestedArchiveFetcher())
    transport = httpx.ASGITransport(app=app)
    token = build_token(settings)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        import_response = await client.post(
            "/api/sources/import",
            json={"urls": ["https://archive.example.com"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert import_response.status_code == 200
        assert import_response.json() == {"inserted": 3, "skipped": 0}

        sources_page = await client.get("/sources", headers={"Authorization": f"Bearer {token}"})
        assert sources_page.status_code == 200
        assert "https://archive.example.com/events/forum-2023" in sources_page.text
        assert "https://archive.example.com/events/forum-2024" in sources_page.text
        assert "https://archive.example.com/events/forum-2025" in sources_page.text


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

        sources_page = await client.get("/sources", headers={"Authorization": f"Bearer {token}"})
        assert sources_page.status_code == 200
        assert "Запустить заново" in sources_page.text
        assert "Jane Smith" in sources_page.text

        jobs_page = await client.get("/jobs", headers={"Authorization": f"Bearer {token}"})
        assert jobs_page.status_code == 200
        assert "https://example.com/conf" in jobs_page.text
        assert "pending" not in jobs_page.text


@pytest.mark.anyio
async def test_sources_page_renders_requeue_button_without_inline_url_js(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)
    settings = build_settings()
    app = create_app(settings, engine=engine, fetcher=StubFetcher())
    transport = httpx.ASGITransport(app=app)
    token = build_token(settings)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        import_response = await client.post(
            "/api/sources/import",
            json={"urls": ["https://example.com/conf?name=alpha&track=main"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert import_response.status_code == 200

        first_run = await client.post(
            "/api/jobs/run-once",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert first_run.status_code == 200
        assert first_run.json() == {"processed": True}

        sources_page = await client.get("/sources", headers={"Authorization": f"Bearer {token}"})
        assert sources_page.status_code == 200
        assert 'data-action="requeue-source"' in sources_page.text
        assert 'data-source-id="1"' in sources_page.text
        assert 'data-seed-url="https://example.com/conf"' in sources_page.text
        assert 'onclick="requeueSource' not in sources_page.text


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
async def test_admin_worker_actions_run_outside_event_loop_thread(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)
    settings = build_settings()
    app = create_app(settings, engine=engine, fetcher=StubFetcher())
    transport = httpx.ASGITransport(app=app)
    token = build_token(settings)
    route_threads: list[str] = []

    def stub_process_next_job(*args, **kwargs):
        route_threads.append(threading.current_thread().name)
        return True

    monkeypatch.setattr("conference_leads_collector.web.app.process_next_job", stub_process_next_job)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/jobs/run-once", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json() == {"processed": True}

    assert route_threads == [route_threads[0]]
    assert route_threads[0] != threading.current_thread().name


@pytest.mark.anyio
async def test_import_sources_runs_outside_event_loop_thread(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)
    settings = build_settings()
    app = create_app(settings, engine=engine, fetcher=StubFetcher())
    transport = httpx.ASGITransport(app=app)
    token = build_token(settings)
    route_threads: list[str] = []

    def stub_expand_seed_urls(urls, fetcher):
        route_threads.append(threading.current_thread().name)
        return urls

    monkeypatch.setattr("conference_leads_collector.web.app.expand_seed_urls", stub_expand_seed_urls)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/sources/import",
            json={"urls": ["https://example.com/conf"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json() == {"inserted": 1, "skipped": 0}

    assert route_threads == [route_threads[0]]
    assert route_threads[0] != threading.current_thread().name


@pytest.mark.anyio
async def test_import_sources_succeeds_when_activity_logging_hits_sqlite_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)
    settings = build_settings()
    app = create_app(settings, engine=engine, fetcher=StubFetcher())
    transport = httpx.ASGITransport(app=app)
    token = build_token(settings)

    def locked_event(*args, **kwargs):
        raise OperationalError("INSERT INTO activity_events", {}, Exception("database is locked"))

    monkeypatch.setattr("conference_leads_collector.web.app.ActivityEventRepository.add_event", locked_event)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/sources/import",
            json={"urls": ["https://example.com/conf"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json() == {"inserted": 1, "skipped": 0}

        sources_page = await client.get("/sources", headers={"Authorization": f"Bearer {token}"})
        assert sources_page.status_code == 200
        assert "https://example.com/conf" in sources_page.text


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
