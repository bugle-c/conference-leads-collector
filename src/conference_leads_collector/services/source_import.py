from __future__ import annotations

from typing import Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit
import re

from bs4 import BeautifulSoup


class Fetcher(Protocol):
    def fetch(self, url: str) -> tuple[int, str]: ...


YEAR_RE = re.compile(r"(20\d{2})")
ARCHIVE_HINTS = ("archive", "архив", "events", "event", "program", "agenda", "conference", "conf", "summit", "forum")
NOISE_HINTS = ("contact", "contacts", "about", "privacy", "policy", "login", "signin", "signup", "career", "vacancy")
PATH_CANDIDATE_RE = re.compile(r'(?:"|\')((?:https?://[^"\']+)|(?:/[A-Za-z0-9._~:/?#\[\]@!$&()*+,;=%-]+))(?:"|\')')


def normalize_import_url(raw_url: str) -> str:
    trimmed = raw_url.strip()
    if "://" not in trimmed:
        trimmed = f"https://{trimmed}"
    parsed = urlsplit(trimmed)
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, "", ""))


def expand_seed_urls(urls: list[str], fetcher: Fetcher | None) -> list[str]:
    expanded_urls: list[str] = []
    seen: set[str] = set()
    for raw_url in urls:
        for expanded_url in _expand_single_seed_url(raw_url, fetcher):
            if expanded_url in seen:
                continue
            seen.add(expanded_url)
            expanded_urls.append(expanded_url)
    return expanded_urls


def _expand_single_seed_url(raw_url: str, fetcher: Fetcher | None) -> list[str]:
    seed_url = normalize_import_url(raw_url)
    if fetcher is None:
        return [seed_url]

    try:
        status_code, html = fetcher.fetch(seed_url)
    except Exception:
        return [seed_url]
    if status_code != 200 or not html:
        return [seed_url]

    candidate_urls = _discover_archive_candidates(seed_url, html, fetcher)
    if _should_expand_archive_index(seed_url, candidate_urls):
        return candidate_urls
    return [seed_url]


def _discover_archive_candidates(seed_url: str, html: str, fetcher: Fetcher) -> list[str]:
    parsed_seed = urlsplit(seed_url)
    candidate_urls: list[str] = []
    seen: set[str] = set()
    hub_urls: list[str] = []
    hub_seen: set[str] = set()

    for resolved, text in _extract_page_links(seed_url, html):
        if urlsplit(resolved).netloc != parsed_seed.netloc:
            continue
        if _looks_like_conference_page(resolved, text):
            if resolved != seed_url and resolved not in seen:
                seen.add(resolved)
                candidate_urls.append(resolved)
            continue
        if _looks_like_archive_hub_page(resolved, text) and resolved not in hub_seen:
            hub_seen.add(resolved)
            hub_urls.append(resolved)

    for sitemap_url in _discover_sitemap_candidates(seed_url, fetcher):
        if sitemap_url == seed_url:
            continue
        if _looks_like_conference_page(sitemap_url):
            if sitemap_url not in seen:
                seen.add(sitemap_url)
                candidate_urls.append(sitemap_url)
            continue
        if _looks_like_archive_hub_page(sitemap_url) and sitemap_url not in hub_seen:
            hub_seen.add(sitemap_url)
            hub_urls.append(sitemap_url)

    for hub_url in hub_urls[:6]:
        try:
            status_code, hub_html = fetcher.fetch(hub_url)
        except Exception:
            continue
        if status_code != 200 or not hub_html:
            continue
        for resolved, text in _extract_page_links(hub_url, hub_html):
            if urlsplit(resolved).netloc != parsed_seed.netloc:
                continue
            if not _looks_like_conference_page(resolved, text):
                continue
            if resolved == seed_url or resolved in seen:
                continue
            seen.add(resolved)
            candidate_urls.append(resolved)

    return sorted(candidate_urls)


def _extract_page_links(base_url: str, html: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    seen: set[str] = set()

    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        resolved = normalize_import_url(urljoin(base_url, href))
        if resolved in seen:
            continue
        seen.add(resolved)
        links.append((resolved, anchor.get_text(" ", strip=True)))

    for raw_candidate in PATH_CANDIDATE_RE.findall(html):
        if raw_candidate.startswith(("mailto:", "tel:", "javascript:")):
            continue
        resolved = normalize_import_url(urljoin(base_url, raw_candidate))
        parsed = urlsplit(resolved)
        if not parsed.netloc or parsed.path in {"", "/"}:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        links.append((resolved, ""))

    return links


def _discover_sitemap_candidates(seed_url: str, fetcher: Fetcher) -> list[str]:
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
        resolved = normalize_import_url(urljoin(seed_url, raw_url.strip()))
        parsed = urlsplit(resolved)
        if parsed.netloc != parsed_seed.netloc:
            continue
        if not _looks_like_conference_page(resolved):
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        candidates.append(resolved)
    return candidates


def _looks_like_conference_page(url: str, text: str = "") -> bool:
    haystack = f"{urlsplit(url).path.lower()} {text.lower()}".strip()
    if any(noise in haystack for noise in NOISE_HINTS):
        return False
    return bool(YEAR_RE.search(haystack)) and any(hint in haystack for hint in ARCHIVE_HINTS)


def _looks_like_archive_hub_page(url: str, text: str = "") -> bool:
    haystack = f"{urlsplit(url).path.lower()} {text.lower()}".strip()
    if any(noise in haystack for noise in NOISE_HINTS):
        return False
    return any(hint in haystack for hint in ARCHIVE_HINTS)


def _should_expand_archive_index(seed_url: str, candidate_urls: list[str]) -> bool:
    if len(candidate_urls) < 2:
        return False

    parsed_seed = urlsplit(seed_url)
    seed_haystack = parsed_seed.path.lower()
    years = {match.group(1) for url in candidate_urls for match in YEAR_RE.finditer(url)}

    if len(years) < 2:
        return False

    if not parsed_seed.path or seed_haystack in {"/", ""}:
        return True

    return any(hint in seed_haystack for hint in ARCHIVE_HINTS)
