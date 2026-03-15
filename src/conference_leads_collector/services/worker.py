from __future__ import annotations

from dataclasses import asdict
from typing import Protocol

import httpx

from conference_leads_collector.extractors.conferences import (
    ConferenceExtractionResult,
    discover_candidate_pages,
    extract_conference_data,
    sanitize_conference_data,
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


def _collect_candidate_pages(fetcher: Fetcher, seed_url: str) -> list[dict[str, str]]:
    status_code, html = fetcher.fetch(seed_url)
    pages = [{"url": seed_url, "status_code": status_code, "html": html}]
    for candidate_url in discover_candidate_pages(seed_url, html)[:8]:
        candidate_status, candidate_html = fetcher.fetch(candidate_url)
        if candidate_status != 200:
            continue
        pages.append({"url": candidate_url, "status_code": candidate_status, "html": candidate_html})
    return pages


def _collect_best_extraction(fetcher: Fetcher, seed_url: str) -> tuple[int, str, ConferenceExtractionResult]:
    pages = _collect_candidate_pages(fetcher, seed_url)
    status_code = pages[0]["status_code"]
    html = pages[0]["html"]
    best_status = status_code
    best_html = html
    best_result = extract_conference_data(seed_url, html)
    best_score = score_extraction(best_result)

    for page in pages[1:]:
        candidate_result = extract_conference_data(page["url"], page["html"])
        candidate_score = score_extraction(candidate_result)
        if candidate_score > best_score:
            best_status = page["status_code"]
            best_html = page["html"]
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
            # Use AI refine only when heuristics found few speakers
            if active_ai_refiner is not None and len(extracted.speakers) < 3:
                try:
                    refined = active_ai_refiner.refine(source.seed_url, html, extracted)
                except Exception as exc:
                    events.add_event(
                        f"AI очистка недоступна для {source.seed_url}",
                        f"Задача #{job.id}: {exc}",
                        level="error",
                    )
                else:
                    if refined.speakers or refined.sponsors:
                        extracted = refined
                        events.add_event(
                            f"AI уточнил данные для {source.seed_url}",
                            f"Задача #{job.id}: эвристика дала мало результатов, AI помог",
                        )
            extracted = sanitize_conference_data(extracted)
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
