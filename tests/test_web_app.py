import time

import httpx
import jwt
import pytest

from conference_leads_collector.config import AppSettings
from conference_leads_collector.storage.db import create_engine, create_schema, session_scope
from conference_leads_collector.storage.repositories import ConferenceSourceRepository, JobRepository
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


@pytest.mark.anyio
async def test_dashboard_includes_action_feedback_ui() -> None:
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
        assert 'id="toast-stack"' in response.text
        assert "showToast(" in response.text
        assert "runAction(" in response.text


@pytest.mark.anyio
async def test_dashboard_uses_compact_layout_and_rounded_ai_values() -> None:
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
                "balance": "22.20375925",
                "total_used": "2.79624075",
                "month_used": None,
                "error": None,
            },
        )
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        assert 'class="dashboard-main-grid"' in response.text
        assert 'class="action-card-grid"' in response.text
        assert "grid-template-columns: minmax(280px, 1fr) minmax(520px, 2fr);" in response.text
        assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in response.text
        assert "22.20" in response.text
        assert "2.80" in response.text
        assert "22.20375925" not in response.text


@pytest.mark.anyio
async def test_dashboard_shows_queue_metrics_instead_of_ambiguous_job_counter() -> None:
    settings = build_settings()
    token = jwt.encode(
        {"sub": "admin", "exp": int(time.time()) + 3600},
        settings.admin_jwt_secret,
        algorithm="HS256",
    )
    app = create_app(settings)
    with session_scope(app.state.engine) as session:
        ConferenceSourceRepository(session).import_seed_urls(["https://example.com/conf"])
        imported_source = ConferenceSourceRepository(session).list_sources_by_urls(["https://example.com/conf"])[0]
        jobs = JobRepository(session)
        pending_job = jobs.enqueue_crawl(imported_source.id)
        running_job = jobs.enqueue_crawl(imported_source.id, force=True)
        running_job.status = "running"
        pending_job.status = "done"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        assert "Всего задач" in response.text
        assert "В очереди" in response.text
        assert "В работе" in response.text
        assert "Последняя задача" in response.text


@pytest.mark.anyio
async def test_dashboard_shows_subtle_activity_timestamps(tmp_path) -> None:
    settings = build_settings()
    token = jwt.encode(
        {"sub": "admin", "exp": int(time.time()) + 3600},
        settings.admin_jwt_secret,
        algorithm="HS256",
    )
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)
    app = create_app(settings, engine=engine)
    with session_scope(app.state.engine) as session:
        ConferenceSourceRepository(session).import_seed_urls(["https://example.com/conf"])

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post(
            "/api/sources/import",
            json={"urls": ["https://example.com/second"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        response = await client.get("/", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        assert 'class="activity-meta"' in response.text
        assert ".activity-meta" in response.text
