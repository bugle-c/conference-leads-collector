from __future__ import annotations

from dataclasses import dataclass

import httpx

from conference_leads_collector.config import AppSettings


@dataclass(slots=True)
class AiGatewayCredits:
    enabled: bool
    balance: str | None = None
    total_used: str | None = None
    month_used: str | None = None
    error: str | None = None


def fetch_ai_gateway_credits(settings: AppSettings) -> AiGatewayCredits:
    if not settings.ai_gateway_api_key:
        return AiGatewayCredits(enabled=False)

    try:
        response = httpx.get(
            f"{settings.ai_gateway_base_url}/credits",
            headers={"Authorization": f"Bearer {settings.ai_gateway_api_key}"},
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return AiGatewayCredits(enabled=True, error="Не удалось загрузить баланс AI Gateway")

    return AiGatewayCredits(
        enabled=True,
        balance=payload.get("balance"),
        total_used=payload.get("total_used"),
        month_used=None,
    )
