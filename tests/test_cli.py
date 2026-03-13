from conference_leads_collector.cli import cmd_run_worker
from conference_leads_collector.config import AppSettings


def build_settings() -> AppSettings:
    return AppSettings(
        app_env="test",
        admin_jwt_secret="super-secret-token-with-32-bytes",
        database_url="sqlite+pysqlite:///unused.db",
        redis_url="redis://localhost:6379/0",
        ai_gateway_api_key="test-key",
    )


def test_cmd_run_worker_passes_settings_to_worker(monkeypatch) -> None:
    captured = {}

    monkeypatch.setattr("conference_leads_collector.cli.create_engine", lambda url: object())
    monkeypatch.setattr("conference_leads_collector.cli.create_schema", lambda engine: None)

    def fake_process_next_job(engine, fetcher=None, settings=None, ai_refiner=None):
        captured["settings"] = settings
        return False

    monkeypatch.setattr("conference_leads_collector.cli.process_next_job", fake_process_next_job)

    exit_code = cmd_run_worker(build_settings(), once=True)

    assert exit_code == 0
    assert captured["settings"].ai_gateway_api_key == "test-key"
