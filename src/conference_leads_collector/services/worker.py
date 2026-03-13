from __future__ import annotations

from dataclasses import asdict
from typing import Protocol

import httpx

from conference_leads_collector.extractors.conferences import extract_conference_data
from conference_leads_collector.storage.db import session_scope
from conference_leads_collector.storage.repositories import ConferenceSourceRepository, JobRepository


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
        job = jobs.claim_next_job()
        if job is None:
            return False

        source = sources.get_source(job.target_id)
        if source is None:
            jobs.mark_failed(job, f"Conference source {job.target_id} not found")
            return True

        try:
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
        except Exception as exc:
            jobs.mark_failed(job, str(exc))

        return True
