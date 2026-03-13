import time

import httpx
import jwt
import pytest

from conference_leads_collector.config import AppSettings
from conference_leads_collector.web.app import create_app


def build_settings() -> AppSettings:
    return AppSettings(
        app_env="test",
        admin_jwt_secret="super-secret-token-with-32-bytes",
        database_url="sqlite+pysqlite:///:memory:",
        redis_url="redis://localhost:6379/0",
    )


@pytest.mark.anyio
async def test_health_endpoint_is_public() -> None:
    transport = httpx.ASGITransport(app=create_app(build_settings()))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "app_env": "test"}


@pytest.mark.anyio
async def test_dashboard_requires_jwt_token() -> None:
    transport = httpx.ASGITransport(app=create_app(build_settings()))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/")

        assert response.status_code == 401
        assert response.json()["detail"] == "Missing bearer token"


@pytest.mark.anyio
async def test_dashboard_accepts_valid_jwt_token() -> None:
    settings = build_settings()
    token = jwt.encode(
        {"sub": "admin", "exp": int(time.time()) + 3600},
        settings.admin_jwt_secret,
        algorithm="HS256",
    )
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        assert "Панель сбора конференций" in response.text


@pytest.mark.anyio
async def test_dashboard_accepts_valid_jwt_token_from_query_param() -> None:
    settings = build_settings()
    token = jwt.encode(
        {"sub": "admin", "exp": int(time.time()) + 3600},
        settings.admin_jwt_secret,
        algorithm="HS256",
    )
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(f"/?token={token}")

        assert response.status_code == 200
        assert "Панель сбора конференций" in response.text
        assert f'/sources?token={token}' in response.text


@pytest.mark.anyio
async def test_dashboard_shows_ai_gateway_credits() -> None:
    settings = build_settings()
    token = jwt.encode(
        {"sub": "admin", "exp": int(time.time()) + 3600},
        settings.admin_jwt_secret,
        algorithm="HS256",
    )
    transport = httpx.ASGITransport(
        app=create_app(
            settings,
            ai_credits_provider=lambda _settings: {
                "enabled": True,
                "balance": "$42.10",
                "total_used": "$7.90",
                "month_used": None,
                "error": None,
            },
        )
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        assert "Баланс AI" in response.text
        assert "$42.10" in response.text
        assert "$7.90" in response.text
