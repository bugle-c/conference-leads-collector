from __future__ import annotations

from dataclasses import dataclass
import re

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


def extract_conference_data(url: str, html: str) -> ConferenceExtractionResult:
    soup = BeautifulSoup(html, "html.parser")
    speakers = _extract_speakers(soup)
    sponsors = _extract_sponsors(soup)
    return ConferenceExtractionResult(speakers=speakers, sponsors=sponsors)


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
                if NAME_RE.match(text):
                    name_text = text
                    break

        if not name_text:
            text = node.get_text(" ", strip=True)
            for chunk in re.split(r"[\n\r]+|(?<=\.)\s+", text):
                chunk = chunk.strip()
                if NAME_RE.match(chunk):
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
        if heading and not any(key in heading for key in ("sponsor", "partner", "спонсор", "партнер")):
            continue

        for card in node.select(".sponsor-card, .partner-card, .partner, .sponsor, img, span, a"):
            name = None
            if card.name == "img":
                name = (card.get("alt") or "").strip()
            elif card.name == "a":
                name = card.get_text(" ", strip=True)
            else:
                img = card.find("img")
                if img and img.get("alt"):
                    name = img.get("alt", "").strip()
                if not name:
                    name = card.get_text(" ", strip=True)

            if not name or len(name) < 2:
                continue
            if name.lower() in {"спонсоры", "sponsors", "partners", "партнеры"}:
                continue
            if name in seen:
                continue
            results.append(SponsorResult(name=name, raw_fragment=str(card)[:2000]))
            seen.add(name)

    return results
