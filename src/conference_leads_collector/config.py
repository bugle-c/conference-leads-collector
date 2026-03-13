from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(slots=True)
class AppSettings:
    app_env: str
    admin_jwt_secret: str
    database_url: str
    redis_url: str
    ai_gateway_api_key: str | None = None
    ai_gateway_base_url: str = "https://ai-gateway.vercel.sh/v1"
    host: str = "0.0.0.0"
    port: int = 8080

    @classmethod
    def from_env(cls) -> "AppSettings":
        app_env = os.getenv("CLC_APP_ENV", "development")
        admin_jwt_secret = os.getenv("CLC_ADMIN_JWT_SECRET", "")
        database_url = _normalize_database_url(os.getenv("CLC_DATABASE_URL", ""))
        redis_url = os.getenv("CLC_REDIS_URL", "")

        missing = [
            key
            for key, value in (
                ("CLC_ADMIN_JWT_SECRET", admin_jwt_secret),
                ("CLC_DATABASE_URL", database_url),
                ("CLC_REDIS_URL", redis_url),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

        return cls(
            app_env=app_env,
            admin_jwt_secret=admin_jwt_secret,
            database_url=database_url,
            redis_url=redis_url,
            ai_gateway_api_key=os.getenv("CLC_AI_GATEWAY_API_KEY") or None,
            ai_gateway_base_url=os.getenv("CLC_AI_GATEWAY_BASE_URL", "https://ai-gateway.vercel.sh/v1").rstrip("/"),
            host=os.getenv("CLC_HOST", "0.0.0.0"),
            port=int(os.getenv("CLC_PORT", "8080")),
        )


def _normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url
