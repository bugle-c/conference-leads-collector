from __future__ import annotations

from dataclasses import dataclass
import json
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup


@dataclass(slots=True)
class TenchatProfileResult:
    profile_url: str
    full_name: str | None
    job_title: str | None
    followers: int | None
    confidence: int = 70
    needs_review: bool = False
    raw_fragment: str | None = None


def extract_public_profile_urls(html: str) -> list[str]:
    if html.lstrip().startswith("<?xml"):
        return _extract_urls_from_rss(html)

    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()
    for link in soup.select("a[href]"):
        href = link.get("href", "").strip()
        normalized = _normalize_tenchat_profile_url(href)
        if not normalized:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        urls.append(normalized)
    return urls


def extract_tenchat_profile(profile_url: str, html: str) -> TenchatProfileResult:
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ", strip=True)

    name, title = _extract_name_and_title(soup, page_text)

    followers = None
    match = re.search(r"(?:подписчики|followers)\s*[:\-]?\s*([\d\s]+)", page_text, re.IGNORECASE)
    if match:
        followers = int(re.sub(r"\s+", "", match.group(1)))
    else:
        counter_match = re.search(r'data-cy="subscriber-counter"[^>]*>(\d[\d\s]*)<', html, re.IGNORECASE)
        if counter_match:
            followers = int(re.sub(r"\s+", "", counter_match.group(1)))

    needs_review = not bool(name and title and followers is not None)
    return TenchatProfileResult(
        profile_url=profile_url,
        full_name=name,
        job_title=title,
        followers=followers,
        needs_review=needs_review,
        raw_fragment=html[:2000],
    )


def _extract_urls_from_rss(xml_text: str) -> list[str]:
    soup = BeautifulSoup(xml_text, "xml")
    urls: list[str] = []
    seen: set[str] = set()
    for item in soup.find_all("item"):
        link_node = item.find("link")
        if not link_node or not link_node.text:
            continue
        normalized = _normalize_tenchat_profile_url(link_node.text.strip())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        urls.append(normalized)
    return urls


def _normalize_tenchat_profile_url(url: str) -> str | None:
    if not url or "tenchat.ru/" not in url:
        return None
    parsed = urlparse(url)
    if "tenchat.ru" not in parsed.netloc:
        return None
    path = parsed.path.rstrip("/")
    if not path or path == "/":
        return None
    if path.startswith("/post/"):
        return f"{parsed.scheme or 'https'}://{parsed.netloc}{path}"
    if path.startswith("/media/"):
        return f"{parsed.scheme or 'https'}://{parsed.netloc}{path}"
    if path.count("/") == 1:
        return f"{parsed.scheme or 'https'}://{parsed.netloc}{path}"
    return None


def _extract_name_and_title(soup: BeautifulSoup, page_text: str) -> tuple[str | None, str | None]:
    header = soup.find(["h1", "h2"])
    fallback_name = header.get_text(" ", strip=True) if header else None

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        objects = payload if isinstance(payload, list) else [payload]
        for item in objects:
            author = item.get("author") if isinstance(item, dict) else None
            if not isinstance(author, dict):
                continue
            given_name = author.get("givenName")
            family_name = author.get("familyName")
            full_name = " ".join(part for part in (given_name, family_name) if part).strip() or fallback_name
            job_title = author.get("jobTitle")
            if full_name or job_title:
                return full_name or fallback_name, job_title

    title = None
    for node in soup.find_all(["div", "p", "span", "meta"]):
        text = node.get("content") if node.name == "meta" else node.get_text(" ", strip=True)
        if not text:
            continue
        lowered = text.lower()
        if any(key in lowered for key in ("marketing", "маркет", "cmo", "brand")):
            title = text
            break

    meta_author = soup.find("meta", attrs={"property": "article:author"})
    if meta_author and meta_author.get("content"):
        return meta_author["content"].strip(), title

    return fallback_name, title
