from __future__ import annotations

from dataclasses import asdict
from typing import Protocol

import httpx

from conference_leads_collector.extractors.conferences import (
    ConferenceExtractionResult,
    discover_candidate_pages,
    extract_conference_data,
    score_extraction,
)
from conference_leads_collector.services.ai_extraction import AiConferenceRefiner
from conference_leads_collector.config import AppSettings
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


def _collect_best_extraction(fetcher: Fetcher, seed_url: str) -> tuple[int, str, ConferenceExtractionResult]:
    status_code, html = fetcher.fetch(seed_url)
    best_status = status_code
    best_html = html
    best_result = extract_conference_data(seed_url, html)
    best_score = score_extraction(best_result)

    for candidate_url in discover_candidate_pages(seed_url, html)[:8]:
        candidate_status, candidate_html = fetcher.fetch(candidate_url)
        if candidate_status != 200:
            continue
        candidate_result = extract_conference_data(candidate_url, candidate_html)
        candidate_score = score_extraction(candidate_result)
        if candidate_score > best_score:
            best_status = candidate_status
            best_html = candidate_html
            best_result = candidate_result
            best_score = candidate_score

    return best_status, best_html, best_result


def _has_high_quality_entities(result: ConferenceExtractionResult) -> bool:
    return bool(result.speakers or result.sponsors)


def process_next_job(engine, fetcher: Fetcher | None = None, settings: AppSettings | None = None, ai_refiner=None) -> bool:
    active_fetcher = fetcher or HttpFetcher()
    active_ai_refiner = ai_refiner
    if active_ai_refiner is None and settings is not None:
        active_ai_refiner = AiConferenceRefiner(settings)
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
            status_code, html, extracted = _collect_best_extraction(active_fetcher, source.seed_url)
            if active_ai_refiner is not None:
                refined = active_ai_refiner.refine(source.seed_url, html, extracted)
                if score_extraction(refined) >= score_extraction(extracted):
                    extracted = refined
            if not _has_high_quality_entities(extracted):
                jobs.mark_failed(job, "No high-quality entities found")
                events.add_event(
                    f"Обработка {source.seed_url} не дала результата",
                    f"HTTP {status_code}, задача #{job.id} не содержит валидных спикеров или спонсоров",
                    level="error",
                )
                return True
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
