from __future__ import annotations

from typing import Protocol
from urllib.parse import quote_plus

import httpx

from conference_leads_collector.extractors.tenchat import extract_public_profile_urls, extract_tenchat_profile
from conference_leads_collector.storage.db import session_scope
from conference_leads_collector.storage.repositories import ActivityEventRepository, TenchatProfileRepository


class TenchatFetcher(Protocol):
    def search(self, query: str) -> str: ...
    def fetch(self, url: str) -> tuple[int, str]: ...


class PublicSearchFetcher:
    def search(self, query: str) -> str:
        query_string = quote_plus(f"site:tenchat.ru {query}")
        headers = {"User-Agent": "Mozilla/5.0"}
        for url in (
            f"https://www.bing.com/search?format=rss&q={query_string}",
            f"https://duckduckgo.com/html/?q={query_string}",
        ):
            response = httpx.get(url, timeout=20.0, follow_redirects=True, headers=headers)
            if response.status_code == 200 and "anomaly.js" not in response.text and "showcaptcha" not in str(response.url):
                return response.text
        return ""

    def fetch(self, url: str) -> tuple[int, str]:
        response = httpx.get(url, timeout=20.0, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
        return response.status_code, response.text


def discover_tenchat_profiles(engine, queries: list[str], fetcher: TenchatFetcher | None = None) -> int:
    active_fetcher = fetcher or PublicSearchFetcher()
    total = 0
    with session_scope(engine) as session:
        repo = TenchatProfileRepository(session)
        events = ActivityEventRepository(session)
        events.add_event(
            f"Запущен поиск TenChat по {len(queries)} запросам",
            ", ".join(queries[:5]) if queries else "Список запросов пуст",
        )
        for query in queries:
            for profile_url in _resolve_profile_urls(active_fetcher, query):
                status_code, html = active_fetcher.fetch(profile_url)
                if status_code != 200:
                    continue
                profile = extract_tenchat_profile(profile_url, html)
                if not _matches_marketing_profile(profile.job_title):
                    continue
                if profile.followers is None or profile.followers < 300:
                    continue
                repo.upsert_profile(
                    profile_url=profile.profile_url,
                    full_name=profile.full_name,
                    job_title=profile.job_title,
                    followers=profile.followers,
                    source_query=query,
                    confidence=profile.confidence,
                    needs_review=profile.needs_review or not (1000 <= profile.followers <= 3000),
                    raw_fragment=profile.raw_fragment,
                )
                total += 1
        events.add_event(
            f"Поиск TenChat завершён: добавлено {total} профилей",
            f"Обработано запросов: {len(queries)}",
        )
    return total


def _resolve_profile_urls(fetcher: TenchatFetcher, query: str) -> list[str]:
    direct_url = query.strip()
    if direct_url.startswith("http://tenchat.ru/") or direct_url.startswith("https://tenchat.ru/"):
        return [direct_url]

    search_html = fetcher.search(query)
    return extract_public_profile_urls(search_html)


def _matches_marketing_profile(job_title: str | None) -> bool:
    if not job_title:
        return False
    lowered = job_title.lower()
    marketing_markers = ("маркет", "marketing", "cmo", "brand")
    return any(part in lowered for part in marketing_markers)
