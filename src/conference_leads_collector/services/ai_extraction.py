from __future__ import annotations

from dataclasses import dataclass
import json
import re

import httpx
from bs4 import BeautifulSoup

from conference_leads_collector.config import AppSettings
from conference_leads_collector.extractors.conferences import (
    ConferenceExtractionResult,
    SpeakerResult,
    SponsorResult,
)


@dataclass(slots=True)
class AiConferenceRefiner:
    settings: AppSettings

    def extract_from_pages(
        self,
        conference_url: str,
        pages: list[dict[str, str]],
    ) -> ConferenceExtractionResult | None:
        if not self.settings.ai_gateway_api_key or not pages:
            return None

        prepared_pages = [
            {
                "url": page["url"],
                "text": BeautifulSoup(page["html"], "html.parser").get_text("\n", strip=True)[:30000],
            }
            for page in pages[:5]
        ]
        instructions = (
            "Ты извлекаешь сущности с сайта конференции. Верни только JSON без пояснений. "
            "Проанализируй несколько страниц одной конференции и выбери, где действительно есть спикеры и спонсоры. "
            "Убери меню, кнопки, тарифы, архивы, CTA, вакансии, email, навигацию и служебный текст. "
            "Спикеры: full_name, first_name, last_name, title, company, regalia_raw. "
            "Спонсоры: name, category, description, website. "
            "Если поле неизвестно, верни пустую строку. Не выдумывай."
        )
        user_prompt = {
            "conference_url": conference_url,
            "pages": prepared_pages,
            "response_schema": {
                "speakers": [
                    {
                        "full_name": "",
                        "first_name": "",
                        "last_name": "",
                        "title": "",
                        "company": "",
                        "regalia_raw": "",
                        "needs_review": False,
                    }
                ],
                "sponsors": [
                    {
                        "name": "",
                        "category": "",
                        "description": "",
                        "website": "",
                        "needs_review": False,
                    }
                ],
            },
        }
        content = self._complete(instructions, user_prompt)
        parsed = _extract_json_object(content)
        result = _build_result_from_payload(parsed)
        return result if result.speakers or result.sponsors else None

    def refine(
        self,
        conference_url: str,
        html: str,
        extracted: ConferenceExtractionResult,
    ) -> ConferenceExtractionResult:
        if not self.settings.ai_gateway_api_key:
            return extracted

        payload = self._request_payload(conference_url, html, extracted)
        content = self._complete(payload["messages"][0]["content"], json.loads(payload["messages"][1]["content"]))
        parsed = _extract_json_object(content)
        result = _build_result_from_payload(parsed)
        if not result.speakers and not result.sponsors:
            return extracted
        return result

    def _complete(self, system_prompt: str, user_payload: dict) -> str:
        response = httpx.post(
            f"{self.settings.ai_gateway_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.settings.ai_gateway_api_key}"},
            json={
                "model": self.settings.ai_gateway_model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ],
            },
            timeout=90.0,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    def _request_payload(
        self,
        conference_url: str,
        html: str,
        extracted: ConferenceExtractionResult,
    ) -> dict:
        page_text = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
        page_text = page_text[:24000]
        candidates = {
            "speakers": [
                {
                    "full_name": item.full_name,
                    "title": item.title,
                    "company": item.company,
                    "regalia_raw": item.regalia_raw,
                }
                for item in extracted.speakers[:40]
            ],
            "sponsors": [{"name": item.name} for item in extracted.sponsors[:80]],
        }
        instructions = (
            "Ты чистишь парсинг конференций. Верни только JSON без пояснений. "
            "Нужно выделить настоящих спикеров и спонсоров, убрать меню, кнопки, email, навигацию, общие фразы и мусор. "
            "Спикеры: full_name, first_name, last_name, title, company, regalia_raw. "
            "Спонсоры: name, category, description, website. "
            "Если поле неизвестно, верни пустую строку. Не выдумывай данные."
        )
        schema_hint = {
            "speakers": [
                {
                    "full_name": "",
                    "first_name": "",
                    "last_name": "",
                    "title": "",
                    "company": "",
                    "regalia_raw": "",
                    "needs_review": False,
                }
            ],
            "sponsors": [
                {
                    "name": "",
                    "category": "",
                    "description": "",
                    "website": "",
                    "needs_review": False,
                }
            ],
        }
        user_prompt = {
            "conference_url": conference_url,
            "heuristic_candidates": candidates,
            "page_text": page_text,
            "response_schema": schema_hint,
        }
        return {
            "model": self.settings.ai_gateway_model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)},
            ],
        }


def _extract_json_object(content: str) -> dict:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.DOTALL)
    return json.loads(content)


def _build_result_from_payload(parsed: dict) -> ConferenceExtractionResult:
    speakers = [
        SpeakerResult(
            full_name=item["full_name"],
            first_name=item.get("first_name"),
            last_name=item.get("last_name"),
            title=item.get("title"),
            company=item.get("company"),
            regalia_raw=item.get("regalia_raw"),
            confidence=90,
            needs_review=item.get("needs_review", False),
        )
        for item in parsed.get("speakers", [])
        if item.get("full_name")
    ]
    sponsors = [
        SponsorResult(
            name=item["name"],
            category=item.get("category"),
            description=item.get("description"),
            website=item.get("website"),
            confidence=85,
            needs_review=item.get("needs_review", False),
        )
        for item in parsed.get("sponsors", [])
        if item.get("name")
    ]
    return ConferenceExtractionResult(speakers=speakers, sponsors=sponsors)
