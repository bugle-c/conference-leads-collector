from __future__ import annotations

from dataclasses import asdict
from typing import Protocol

import httpx

from conference_leads_collector.extractors.conferences import extract_conference_data
from conference_leads_collector.storage.db import session_scope
from conference_leads_collector.storage.repositories import ActivityEventRepository, ConferenceSourceRepository, JobRepository


class Fetcher(Protocol):
    def fetch(self, url: str) -> tuple[int, str]: ...


class HttpFetcher:
    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout

    def fetch(self, url: str) -> tuple[int, str]:
        response = httpx.get(url, timeout=self.timeout, follow_redirects=True)
        return response.status_code, response.text


def process_next_job(engine, fetcher: Fetcher | None = None) -> bool:
    active_fetcher = fetcher or HttpFetcher()
    with session_scope(engine) as session:
        jobs = JobRepository(session)
        sources = ConferenceSourceRepository(session)
        events = ActivityEventRepository(session)
        job = jobs.claim_next_job()
        if job is None:
            events.add_event("Новых задач в очереди нет")
            return False

        source = sources.get_source(job.target_id)
        if source is None:
            jobs.mark_failed(job, f"Conference source {job.target_id} not found")
            events.add_event(
                f"Задача #{job.id} не выполнена",
                f"Источник конференции {job.target_id} не найден",
                level="error",
            )
            return True

        try:
            events.add_event(
                f"Запущена обработка конференции {source.seed_url}",
                f"Задача #{job.id} взята в работу",
            )
            status_code, html = active_fetcher.fetch(source.seed_url)
            extracted = extract_conference_data(source.seed_url, html)
            sources.mark_crawled(
                source.id,
                source.seed_url,
                status_code,
                html,
                [asdict(item) for item in extracted.speakers],
                [asdict(item) for item in extracted.sponsors],
            )
            jobs.mark_done(job)
            events.add_event(
                f"Обработка {source.seed_url} завершена: {len(extracted.speakers)} спикеров, {len(extracted.sponsors)} спонсоров",
                f"HTTP {status_code}, задача #{job.id} завершена успешно",
            )
        except Exception as exc:
            jobs.mark_failed(job, str(exc))
            events.add_event(
                f"Обработка {source.seed_url} завершилась с ошибкой",
                str(exc),
                level="error",
            )

        return True
