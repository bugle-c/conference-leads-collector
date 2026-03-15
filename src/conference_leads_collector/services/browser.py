from __future__ import annotations

import base64
from dataclasses import dataclass
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from conference_leads_collector.extractors.conferences import discover_candidate_pages


@dataclass(slots=True)
class RenderedPage:
    url: str
    html: str
    screenshot_b64: str
    status: int = 200


@dataclass(slots=True)
class BrowserRenderer:
    timeout_ms: int = 30000
    screenshot_width: int = 1280
    max_subpages: int = 3

    def render_conference(self, seed_url: str) -> list[RenderedPage]:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    viewport={"width": self.screenshot_width, "height": 800},
                    locale="ru-RU",
                )
                page = context.new_page()
                pages: list[RenderedPage] = []

                # Render seed page
                seed_page = self._render_page(page, seed_url)
                if seed_page is None:
                    return []
                pages.append(seed_page)

                # Discover and render subpages
                subpage_urls = self._discover_subpages(seed_url, seed_page.html)
                index = 0
                while index < len(subpage_urls) and len(pages) < self.max_subpages + 1:
                    sub_url = subpage_urls[index]
                    index += 1
                    sub_page = self._render_page(page, sub_url)
                    if sub_page is not None:
                        pages.append(sub_page)
                        if self._looks_like_hub_page(sub_url):
                            for nested_url in self._discover_subpages(sub_url, sub_page.html):
                                if nested_url not in {page.url for page in pages} and nested_url not in subpage_urls:
                                    subpage_urls.append(nested_url)

                return pages
            finally:
                browser.close()

    def _render_page(self, page, url: str) -> RenderedPage | None:
        try:
            page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            # Scroll to trigger lazy-loading
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(500)

            screenshot_bytes = page.screenshot(full_page=True)
            html = page.content()
            return RenderedPage(
                url=url,
                html=html,
                screenshot_b64=base64.b64encode(screenshot_bytes).decode(),
            )
        except Exception:
            return None

    def _discover_subpages(self, seed_url: str, html: str) -> list[str]:
        discovered = discover_candidate_pages(seed_url, html)
        candidates: list[str] = []
        seen: set[str] = {seed_url}

        for candidate_url in discovered:
            if candidate_url in seen:
                continue
            seen.add(candidate_url)
            candidates.append(candidate_url)

        return candidates

    def _looks_like_hub_page(self, url: str) -> bool:
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
