from __future__ import annotations

import base64
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import sync_playwright


DISCOVERY_KEYWORDS = (
    "speaker", "speakers", "спикер", "спикеры",
    "program", "agenda", "программа",
    "sponsor", "partner", "спонсор", "партнер", "партнёр",
    "archive", "архив",
    "expert", "experts", "эксперт",
    "committee", "комитет",
)


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
                for sub_url in subpage_urls[: self.max_subpages]:
                    sub_page = self._render_page(page, sub_url)
                    if sub_page is not None:
                        pages.append(sub_page)

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
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        parsed_seed = urlsplit(seed_url)
        candidates: list[str] = []
        seen: set[str] = {seed_url}

        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href", "").strip()
            if not href or href.startswith("#") or href.startswith("mailto:"):
                continue
            text = anchor.get_text(" ", strip=True).lower()
            resolved = urljoin(seed_url, href)
            parsed = urlsplit(resolved)
            if parsed.netloc and parsed.netloc != parsed_seed.netloc:
                continue
            haystack = f"{text} {parsed.path.lower()}"
            if not any(kw in haystack for kw in DISCOVERY_KEYWORDS):
                continue
            normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/') or parsed.path}"
            if normalized in seen:
                continue
            seen.add(normalized)
            candidates.append(normalized)

        return candidates
