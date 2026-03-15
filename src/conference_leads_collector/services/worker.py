from __future__ import annotations

from queue import Queue
from threading import Thread
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


def _render_conference_pages(seed_url: str, renderer_factory=None) -> list:
    if renderer_factory is None:
        from conference_leads_collector.services.browser import BrowserRenderer

        renderer_factory = BrowserRenderer

    result_queue: Queue[tuple[bool, object]] = Queue(maxsize=1)

    def runner() -> None:
        try:
            rendered_pages = renderer_factory().render_conference(seed_url)
        except Exception as exc:
            result_queue.put((False, exc))
            return
        result_queue.put((True, rendered_pages))

    thread = Thread(target=runner, name="clc-browser-render", daemon=True)
    thread.start()
    thread.join()
    success, payload = result_queue.get()
    if success:
        return payload
    raise payload


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


def _merge_results(
    vision: ConferenceExtractionResult | None,
    text: ConferenceExtractionResult | None,
) -> ConferenceExtractionResult:
    """Merge vision (primary) and text (supplement) results. Vision takes priority."""
    speakers: list = []
    sponsors: list = []
    seen_speakers: set[str] = set()
    seen_sponsors: set[str] = set()

    # Vision results first (higher confidence, cleaner)
    for source in [vision, text]:
        if source is None:
            continue
        for s in source.speakers:
            key = s.full_name.lower().strip()
            sorted_key = " ".join(sorted(key.split()))
            if key not in seen_speakers and sorted_key not in seen_speakers:
                speakers.append(s)
                seen_speakers.add(key)
                seen_speakers.add(sorted_key)
        for s in source.sponsors:
            key = s.name.lower().strip()
            if key not in seen_sponsors:
                sponsors.append(s)
                seen_sponsors.add(key)

    return ConferenceExtractionResult(speakers=speakers, sponsors=sponsors)


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

        sources.mark_running(source.id)

        try:
            events.add_event(
                f"Запущена обработка конференции {source.seed_url}",
                f"Задача #{job.id} взята в работу",
            )

            vision_result = None
            text_result = None
            status_code = 0
            html = ""

            # --- Vision pipeline (primary) ---
            if settings is not None and settings.ai_gateway_api_key:
                try:
                    from conference_leads_collector.services.vision_extraction import VisionExtractor

                    rendered_pages = _render_conference_pages(source.seed_url)

                    if rendered_pages:
                        status_code = 200
                        html = rendered_pages[0].html

                        # Vision extraction from screenshots
                        vision_extractor = VisionExtractor(settings)
                        screenshots = [
                            {"url": rp.url, "screenshot_b64": rp.screenshot_b64}
                            for rp in rendered_pages
                        ]
                        vision_result = vision_extractor.extract_from_screenshots(
                            source.seed_url, screenshots
                        )
                        events.add_event(
                            f"Vision извлёк данные для {source.seed_url}",
                            f"Задача #{job.id}: {len(vision_result.speakers)} спикеров, "
                            f"{len(vision_result.sponsors)} спонсоров из {len(rendered_pages)} скриншотов",
                        )

                        # Text supplement from rendered HTML
                        if active_ai_refiner is not None:
                            try:
                                text_pages = [
                                    {"url": rp.url, "html": rp.html}
                                    for rp in rendered_pages
                                ]
                                text_result = active_ai_refiner.extract_from_rendered_text(
                                    source.seed_url, text_pages
                                )
                            except Exception as exc:
                                events.add_event(
                                    f"Text supplement недоступен для {source.seed_url}",
                                    f"Задача #{job.id}: {exc}",
                                    level="error",
                                )
                except Exception as exc:
                    events.add_event(
                        f"Vision pipeline недоступен для {source.seed_url}",
                        f"Задача #{job.id}: {exc}, переход на text pipeline",
                        level="error",
                    )

            # --- Fallback: text pipeline (if vision didn't run or failed) ---
            if vision_result is None:
                status_code, html, extracted = _collect_best_extraction(
                    active_fetcher, source.seed_url
                )
                if active_ai_refiner is not None:
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
                text_result = extracted

            # --- Merge and sanitize ---
            merged = _merge_results(vision_result, text_result)
            extracted = sanitize_conference_data(merged)

            if not _has_high_quality_entities(extracted):
                jobs.mark_failed(job, "No high-quality entities found")
                sources.mark_failed(source.id, "No high-quality entities found")
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
            sources.mark_failed(source.id, str(exc))
            events.add_event(
                f"Обработка {source.seed_url} завершилась с ошибкой",
                str(exc),
                level="error",
            )

        return True
