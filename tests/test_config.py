import os

import pytest

from conference_leads_collector.config import AppSettings


def test_loads_required_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLC_APP_ENV", "test")
    monkeypatch.setenv("CLC_ADMIN_JWT_SECRET", "secret-value")
    monkeypatch.setenv("CLC_DATABASE_URL", "postgresql://user:pass@localhost:5432/clc")
    monkeypatch.setenv("CLC_REDIS_URL", "redis://localhost:6379/0")

    settings = AppSettings.from_env()

    assert settings.app_env == "test"
    assert settings.admin_jwt_secret == "secret-value"
    assert settings.database_url == "postgresql+psycopg://user:pass@localhost:5432/clc"
    assert settings.redis_url == "redis://localhost:6379/0"


def test_raises_clear_error_when_required_settings_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "CLC_APP_ENV",
        "CLC_ADMIN_JWT_SECRET",
        "CLC_DATABASE_URL",
        "CLC_REDIS_URL",
    ):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ValueError) as exc_info:
        AppSettings.from_env()

    assert "CLC_ADMIN_JWT_SECRET" in str(exc_info.value)
    assert "CLC_DATABASE_URL" in str(exc_info.value)
    assert "CLC_REDIS_URL" in str(exc_info.value)
