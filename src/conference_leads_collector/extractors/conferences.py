from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag


@dataclass(slots=True)
class SpeakerResult:
    full_name: str
    first_name: str | None
    last_name: str | None
    title: str | None
    company: str | None
    regalia_raw: str | None
    confidence: int = 80
    needs_review: bool = False
    raw_fragment: str | None = None


@dataclass(slots=True)
class SponsorResult:
    name: str
    category: str | None = None
    description: str | None = None
    website: str | None = None
    confidence: int = 70
    needs_review: bool = False
    raw_fragment: str | None = None


@dataclass(slots=True)
class ConferenceExtractionResult:
    speakers: list[SpeakerResult]
    sponsors: list[SponsorResult]


NAME_RE = re.compile(r"^[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё'-]+(?:\s+[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё'-]+){1,3}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DISCOVERY_KEYWORDS = (
    "speaker",
    "speakers",
    "спикер",
    "спикеры",
    "program",
    "agenda",
    "программа",
    "archive",
    "архив",
    "expert",
    "experts",
    "эксперт",
    "committee",
    "комитет",
    "session",
    "sessions",
    "track",
    "tracks",
)
SPEAKER_NOISE_EXACT = {
    "registration",
    "register",
    "ru en",
    "en ru",
    "купить билет",
    "подать доклад",
    "программа",
    "спикеры",
    "program",
    "speakers",
}
SPEAKER_NOISE_PARTS = (
    "ticket",
    "register",
    "registration",
    "buy ticket",
    "submit",
    "доклад",
    "билет",
)
ORG_WORDS = {
    "university",
    "institute",
    "electronics",
    "laboratory",
    "labs",
    "tech",
    "school",
    "cloud",
    "forum",
    "conference",
    "softline",
    "samsung",
    "carnegie",
    "mellon",
}
SPONSOR_SECTION_KEYWORDS = ("sponsor", "partner", "спонсор", "партнер")
SPONSOR_NOISE_EXACT = {
    "спикеры",
    "speaker",
    "speakers",
    "программа",
    "program",
    "agenda",
    "купить билет",
    "tickets",
    "ticket",
    "контакты",
    "contacts",
    "о мероприятии",
    "about",
    "telegram",
    "vk",
    "вконтакте",
    "подробнее",
    "читать далее",
    "смотреть все",
    "узнать больше",
    "главная",
    "меню",
}
SPONSOR_NOISE_PARTS = (
    "@",
    "mailto:",
    "http://",
    "https://",
)


def _split_name(full_name: str) -> tuple[str | None, str | None]:
    parts = full_name.split()
    if len(parts) >= 2:
        return parts[0], parts[-1]
    return None, None


def _parse_title_company(text: str) -> tuple[str | None, str | None]:
    cleaned = " ".join(text.split())
    if "," in cleaned:
        left, right = [part.strip() for part in cleaned.split(",", 1)]
        return left or None, right or None
    return cleaned or None, None


def _heading_text(node: Tag) -> str:
    header = node.find(["h1", "h2", "h3", "h4"])
    return header.get_text(" ", strip=True).lower() if header else ""


def _normalize_text(text: str) -> str:
    return " ".join(text.split()).strip()


def _is_probably_speaker_name(value: str) -> bool:
    normalized = _normalize_text(value)
    lowered = normalized.lower()
    if not NAME_RE.match(normalized):
        return False
    if lowered in SPEAKER_NOISE_EXACT:
        return False
    if any(part in lowered for part in SPEAKER_NOISE_PARTS):
        return False
    tokens = [token.lower() for token in normalized.split()]
    if all(token in ORG_WORDS for token in tokens):
        return False
    return True


def _is_probably_sponsor_name(value: str) -> bool:
    normalized = _normalize_text(value)
    lowered = normalized.lower()

    if not normalized or len(normalized) < 2:
        return False
    if EMAIL_RE.match(lowered):
        return False
    if lowered in SPONSOR_NOISE_EXACT:
        return False
    if any(part in lowered for part in SPONSOR_NOISE_PARTS):
        return False
    if len(normalized) > 64:
        return False
    if len(normalized.split()) > 4:
        return False
    if re.search(r"[.!?]", normalized):
        return False
    if re.fullmatch(r"[\d\s\-+]+", normalized):
        return False
    return True


def extract_conference_data(url: str, html: str) -> ConferenceExtractionResult:
    soup = BeautifulSoup(html, "html.parser")
    speakers = _extract_speakers(soup)
    sponsors = _extract_sponsors(soup)
    return ConferenceExtractionResult(speakers=speakers, sponsors=sponsors)


def discover_candidate_pages(seed_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    parsed_seed = urlsplit(seed_url)
    candidates: list[str] = []
    seen: set[str] = {seed_url}

    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
            continue
        text = anchor.get_text(" ", strip=True).lower()
        resolved = urljoin(seed_url, href)
        parsed = urlsplit(resolved)
        if parsed.netloc and parsed.netloc != parsed_seed.netloc:
            continue
        haystack = f"{text} {parsed.path.lower()}"
        if not any(keyword in haystack for keyword in DISCOVERY_KEYWORDS):
            continue
        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/') or parsed.path}"
        if normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(normalized)

    return candidates


def score_extraction(result: ConferenceExtractionResult) -> int:
    return len(result.speakers) * 10 + len(result.sponsors) * 6


def _extract_speakers(soup: BeautifulSoup) -> list[SpeakerResult]:
    results: list[SpeakerResult] = []
    seen: set[str] = set()

    candidates = soup.select(".speaker-card, .speaker, article, .member, .team-card")
    if not candidates:
        candidates = []
        for section in soup.find_all(["section", "div"]):
            heading = _heading_text(section)
            classes = " ".join(section.get("class", []))
            if any(key in heading for key in ("speaker", "спикер")) or "speaker" in classes:
                candidates.extend(section.find_all(["article", "div", "li"], recursive=True))

    for node in candidates:
        class_text = " ".join(node.get("class", []))
        if any(key in class_text for key in ("sponsor", "partner")):
            continue

        name_text = None
        for selector in ("h3", "h2", ".name", ".speaker-name", "strong", "b"):
            name_node = node.select_one(selector)
            if name_node:
                text = name_node.get_text(" ", strip=True)
                if _is_probably_speaker_name(text):
                    name_text = text
                    break

        if not name_text:
            text = node.get_text(" ", strip=True)
            for chunk in re.split(r"[\n\r]+|(?<=\.)\s+", text):
                chunk = chunk.strip()
                if _is_probably_speaker_name(chunk):
                    name_text = chunk
                    break

        if not name_text or name_text in seen:
            continue

        text_blocks = [tag.get_text(" ", strip=True) for tag in node.find_all(["p", "span", "div", "li"]) if tag.get_text(" ", strip=True)]
        detail_text = next((text for text in text_blocks if text != name_text), "")
        title, company = _parse_title_company(detail_text)
        first_name, last_name = _split_name(name_text)
        results.append(
            SpeakerResult(
                full_name=name_text,
                first_name=first_name,
                last_name=last_name,
                title=title,
                company=company,
                regalia_raw=detail_text or None,
                raw_fragment=str(node)[:2000],
            )
        )
        seen.add(name_text)

    return results


def _extract_sponsors(soup: BeautifulSoup) -> list[SponsorResult]:
    results: list[SponsorResult] = []
    seen: set[str] = set()

    containers = soup.select(".sponsor-card, .partner-card, .partner, .sponsor, section, div")
    for node in containers:
        heading = _heading_text(node)
        if heading and not any(key in heading for key in SPONSOR_SECTION_KEYWORDS):
            continue

        explicit_cards = node.select(".sponsor-card, .partner-card, .partner, .sponsor, li")
        cards = explicit_cards or node.select("img")

        for card in cards:
            name = None
            if card.name == "img":
                name = _normalize_text(card.get("alt") or "")
            else:
                img = card.find("img")
                if img and img.get("alt"):
                    name = _normalize_text(img.get("alt", ""))
                if not name:
                    name = _normalize_text(card.get_text(" ", strip=True))

            if not _is_probably_sponsor_name(name or ""):
                continue
            if name.lower() in {"спонсоры", "sponsors", "partners", "партнеры"}:
                continue
            if name in seen:
                continue
            results.append(SponsorResult(name=name, raw_fragment=str(card)[:2000]))
            seen.add(name)

    return results
