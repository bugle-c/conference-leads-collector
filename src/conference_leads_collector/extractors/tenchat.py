from __future__ import annotations

from dataclasses import dataclass
import re

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
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()
    for link in soup.select("a[href]"):
        href = link.get("href", "").strip()
        if "tenchat.ru/" not in href:
            continue
        if href in seen:
            continue
        seen.add(href)
        urls.append(href)
    return urls


def extract_tenchat_profile(profile_url: str, html: str) -> TenchatProfileResult:
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ", strip=True)

    name = None
    header = soup.find(["h1", "h2"])
    if header:
        name = header.get_text(" ", strip=True) or None

    title = None
    for node in soup.find_all(["div", "p", "span"]):
        text = node.get_text(" ", strip=True)
        lowered = text.lower()
        if any(key in lowered for key in ("marketing", "маркет", "cmo", "brand")):
            title = text
            break

    followers = None
    match = re.search(r"(?:подписчики|followers)\s*[:\-]?\s*([\d\s]+)", page_text, re.IGNORECASE)
    if match:
        followers = int(re.sub(r"\s+", "", match.group(1)))

    needs_review = not bool(name and title and followers is not None)
    return TenchatProfileResult(
        profile_url=profile_url,
        full_name=name,
        job_title=title,
        followers=followers,
        needs_review=needs_review,
        raw_fragment=html[:2000],
    )
