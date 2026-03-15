from __future__ import annotations

from queue import Queue
from threading import Thread
from dataclasses import asdict
from typing import Protocol
from urllib.parse import urljoin, urlsplit
import re

import httpx

from conference_leads_collector.extractors.conferences import (
    ConferenceExtractionResult,
    DISCOVERY_KEYWORDS,
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
    candidate_urls = discover_candidate_pages(seed_url, html)
    if len(candidate_urls) < 3:
        candidate_urls.extend(_discover_sitemap_pages(fetcher, seed_url))

    seen_urls = {seed_url}
    pending_urls: list[str] = []
    for candidate_url in candidate_urls:
        if candidate_url in seen_urls:
            continue
        seen_urls.add(candidate_url)
        pending_urls.append(candidate_url)

    index = 0
    while index < len(pending_urls) and len(pages) < 13:
        candidate_url = pending_urls[index]
        index += 1
        try:
            candidate_status, candidate_html = fetcher.fetch(candidate_url)
        except Exception:
            continue
        if candidate_status != 200:
            continue
        pages.append({"url": candidate_url, "status_code": candidate_status, "html": candidate_html})
        if _looks_like_hub_page(candidate_url):
            for nested_url in discover_candidate_pages(candidate_url, candidate_html):
                if nested_url in seen_urls:
                    continue
                seen_urls.add(nested_url)
                pending_urls.append(nested_url)
    return pages


def _looks_like_hub_page(url: str) -> bool:
    path = urlsplit(url).path.lower().rstrip("/")
    if not path:
        return False
    return any(
        keyword in path
        for keyword in (
            "/events",
            "/event",
            "/archive",
            "/program",
            "/agenda",
            "/conference",
            "/conferences",
            "/camp",
            "/forum",
            "/summit",
            "/мероприят",
            "/архив",
            "/программа",
        )
    )


def _discover_sitemap_pages(fetcher: Fetcher, seed_url: str) -> list[str]:
    parsed_seed = urlsplit(seed_url)
    sitemap_url = f"{parsed_seed.scheme}://{parsed_seed.netloc}/sitemap.xml"
    try:
        status_code, xml_text = fetcher.fetch(sitemap_url)
    except Exception:
        return []
    if status_code != 200 or "<loc>" not in xml_text:
        return []

    candidates: list[str] = []
    seen: set[str] = set()
    for raw_url in re.findall(r"<loc>(.*?)</loc>", xml_text, flags=re.IGNORECASE):
        resolved = urljoin(seed_url, raw_url.strip())
        parsed = urlsplit(resolved)
        if parsed.netloc != parsed_seed.netloc:
            continue
        haystack = parsed.path.lower()
        if not any(keyword in haystack for keyword in DISCOVERY_KEYWORDS):
            continue
        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/') or parsed.path}"
        if normalized in seen or normalized == seed_url:
            continue
        seen.add(normalized)
        candidates.append(normalized)
    return candidates


def _collect_best_extraction(fetcher: Fetcher, seed_url: str) -> tuple[int, str, ConferenceExtractionResult, list[dict[str, str]]]:
    pages = _collect_candidate_pages(fetcher, seed_url)
    status_code = pages[0]["status_code"]
    html = pages[0]["html"]
    best_status = status_code
    best_html = html
    best_result = extract_conference_data(seed_url, html)
    best_score = score_extraction(best_result)
    merged_result = best_result

    for page in pages[1:]:
        candidate_result = extract_conference_data(page["url"], page["html"])
        merged_result = _merge_results(merged_result, candidate_result)
        candidate_score = score_extraction(candidate_result)
        if candidate_score > best_score:
            best_status = page["status_code"]
            best_html = page["html"]
            best_result = candidate_result
            best_score = candidate_score

    return best_status, best_html, merged_result, pages


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


def _is_external_blocker(url: str, error: str) -> bool:
    lowered_url = url.lower()
    lowered_error = error.lower()

    if lowered_url.endswith(".pdf") or "t.me/" in lowered_url:
        return True

    blocker_signals = (
        "certificate_verify_failed",
        "hostname mismatch",
        "name or service not known",
        "handshake operation timed out",
        "temporary failure in name resolution",
        "tls",
        "ssl",
    )
    return any(signal in lowered_error for signal in blocker_signals)


def _build_blocked_note(url: str, error: str) -> str:
    lowered_url = url.lower()
    lowered_error = error.lower()

    if lowered_url.endswith(".pdf"):
        return "External blocker: PDF source is not yet supported"
    if "t.me/" in lowered_url:
        return "External blocker: Telegram pages are not crawlable via current public pipeline"
    if "name or service not known" in lowered_error or "temporary failure in name resolution" in lowered_error:
        return "External blocker: DNS resolution failed"
    if "certificate_verify_failed" in lowered_error or "hostname mismatch" in lowered_error:
        return "External blocker: SSL certificate mismatch"
    if "handshake operation timed out" in lowered_error:
        return "External blocker: TLS handshake timed out"
    return f"External blocker: {error}"


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
                status_code, html, extracted, pages = _collect_best_extraction(
                    active_fetcher, source.seed_url
                )
                if active_ai_refiner is not None:
                    if (
                        hasattr(active_ai_refiner, "extract_from_pages")
                        and (len(extracted.speakers) < 3 or not extracted.sponsors)
                    ):
                        try:
                            page_refined = active_ai_refiner.extract_from_pages(source.seed_url, pages)
                        except Exception as exc:
                            events.add_event(
                                f"AI добор по нескольким страницам недоступен для {source.seed_url}",
                                f"Задача #{job.id}: {exc}",
                                level="error",
                            )
                        else:
                            if page_refined is not None:
                                extracted = _merge_results(extracted, page_refined)
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
                error_note = "No high-quality entities found"
                if _is_external_blocker(source.seed_url, error_note):
                    blocked_note = _build_blocked_note(source.seed_url, error_note)
                    jobs.mark_failed(job, blocked_note)
                    sources.mark_blocked(source.id, blocked_note)
                else:
                    jobs.mark_failed(job, error_note)
                    sources.mark_failed(source.id, error_note)
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
            error_text = str(exc)
            if _is_external_blocker(source.seed_url, error_text):
                blocked_note = _build_blocked_note(source.seed_url, error_text)
                jobs.mark_failed(job, blocked_note)
                sources.mark_blocked(source.id, blocked_note)
            else:
                jobs.mark_failed(job, error_text)
                sources.mark_failed(source.id, error_text)
            events.add_event(
                f"Обработка {source.seed_url} завершилась с ошибкой",
                error_text,
                level="error",
            )

        return True
