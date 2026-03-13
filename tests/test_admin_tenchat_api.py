import time
from pathlib import Path

import httpx
import jwt
import pytest

from conference_leads_collector.config import AppSettings
from conference_leads_collector.storage.db import create_engine, create_schema
from conference_leads_collector.web.app import create_app


class StubTenChatFetcher:
    def search(self, query: str) -> str:
        return """
        <html><body>
          <a href="https://tenchat.ru/media/12345-jane-smith">Jane Smith</a>
        </body></html>
        """

    def fetch(self, url: str):
        return 200, """
        <html><body><h1>Jane Smith</h1><div>Head of Marketing</div><div>Подписчики: 2 100</div></body></html>
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
async def test_discover_tenchat_profiles_from_public_search(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)
    settings = build_settings()
    app = create_app(settings, engine=engine, fetcher=StubTenChatFetcher())
    transport = httpx.ASGITransport(app=app)
    token = build_token(settings)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/tenchat/discover",
            json={"queries": ["директор по маркетингу"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json()["profiles_found"] == 1

        dashboard_response = await client.get("/", headers={"Authorization": f"Bearer {token}"})
        assert dashboard_response.status_code == 200
        assert "Запущен поиск TenChat по 1 запросам" in dashboard_response.text
        assert "Поиск TenChat завершён: добавлено 1 профилей" in dashboard_response.text

        page_response = await client.get("/tenchat", headers={"Authorization": f"Bearer {token}"})
        assert page_response.status_code == 200
        assert "Jane Smith" in page_response.text
        assert "2100" in page_response.text
