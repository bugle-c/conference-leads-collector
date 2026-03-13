from conference_leads_collector.config import AppSettings
from conference_leads_collector.services.ai_extraction import AiConferenceRefiner


class FailingAiRefiner(AiConferenceRefiner):
    def _complete(self, system_prompt: str, user_payload: dict) -> str:
        raise RuntimeError("gateway timeout")


def build_settings() -> AppSettings:
    return AppSettings(
        app_env="test",
        admin_jwt_secret="super-secret-token-with-32-bytes",
        database_url="sqlite+pysqlite:///unused.db",
        redis_url="redis://localhost:6379/0",
        ai_gateway_api_key="test-key",
    )


def test_ai_extract_from_pages_propagates_gateway_errors() -> None:
    refiner = FailingAiRefiner(build_settings())

    try:
        refiner.extract_from_pages(
            "https://example.com/conf",
            [{"url": "https://example.com/conf", "html": "<html><body>conf</body></html>"}],
        )
    except RuntimeError as exc:
        assert str(exc) == "gateway timeout"
    else:
        raise AssertionError("extract_from_pages should propagate gateway errors")


def test_ai_refine_propagates_gateway_errors() -> None:
    refiner = FailingAiRefiner(build_settings())

    try:
        refiner.refine("https://example.com/conf", "<html><body>conf</body></html>", extracted=type("Result", (), {"speakers": [], "sponsors": []})())
    except RuntimeError as exc:
        assert str(exc) == "gateway timeout"
    else:
        raise AssertionError("refine should propagate gateway errors")
