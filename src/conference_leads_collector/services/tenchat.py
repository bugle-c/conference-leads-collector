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
        url = f"https://duckduckgo.com/html/?q={quote_plus(f'site:tenchat.ru {query}')}"
        return httpx.get(url, timeout=20.0, follow_redirects=True).text

    def fetch(self, url: str) -> tuple[int, str]:
        response = httpx.get(url, timeout=20.0, follow_redirects=True)
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
            search_html = active_fetcher.search(query)
            for profile_url in extract_public_profile_urls(search_html):
                status_code, html = active_fetcher.fetch(profile_url)
                if status_code != 200:
                    continue
                profile = extract_tenchat_profile(profile_url, html)
                repo.upsert_profile(
                    profile_url=profile.profile_url,
                    full_name=profile.full_name,
                    job_title=profile.job_title,
                    followers=profile.followers,
                    source_query=query,
                    confidence=profile.confidence,
                    needs_review=profile.needs_review,
                    raw_fragment=profile.raw_fragment,
                )
                total += 1
        events.add_event(
            f"Поиск TenChat завершён: добавлено {total} профилей",
            f"Обработано запросов: {len(queries)}",
        )
    return total
