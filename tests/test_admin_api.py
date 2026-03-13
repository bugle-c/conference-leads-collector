import time
from pathlib import Path

import httpx
import jwt
import pytest

from conference_leads_collector.config import AppSettings
from conference_leads_collector.storage.db import create_engine, create_schema
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
