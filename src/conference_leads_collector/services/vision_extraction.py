from __future__ import annotations

import json
import re
from dataclasses import dataclass

import httpx

from conference_leads_collector.config import AppSettings
from conference_leads_collector.extractors.conferences import (
    ConferenceExtractionResult,
    SpeakerResult,
    SponsorResult,
)

VISION_SYSTEM_PROMPT = (
    "Ты анализируешь скриншоты сайта конференции. "
    "Извлеки ВСЕХ спикеров и спонсоров/партнёров. "
    "Спикеры: найди карточки с фото, имена в программе, списки выступающих. "
    "Спонсоры: найди логотипы партнёров, спонсоров, организаторов. "
    "Верни ТОЛЬКО JSON без пояснений. Формат:\n"
    '{"speakers": [{"full_name": "", "title": "", "company": ""}], '
    '"sponsors": [{"name": "", "category": ""}]}\n'
    "Если поле неизвестно — пустая строка. Не выдумывай данных."
)


@dataclass(slots=True)
class VisionExtractor:
    settings: AppSettings

    def extract_from_screenshots(
        self,
        conference_url: str,
        screenshots: list[dict[str, str]],
    ) -> ConferenceExtractionResult:
        if not self.settings.ai_gateway_api_key or not screenshots:
            return ConferenceExtractionResult(speakers=[], sponsors=[])

        content_parts: list[dict] = [
            {"type": "text", "text": f"Конференция: {conference_url}. Найди всех спикеров и спонсоров:"},
        ]
        for ss in screenshots[:4]:
            content_parts.append({"type": "text", "text": f"Страница: {ss['url']}"})
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{ss['screenshot_b64']}"},
            })

        response = httpx.post(
            f"{self.settings.ai_gateway_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.settings.ai_gateway_api_key}"},
            json={
                "model": self.settings.ai_gateway_model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": VISION_SYSTEM_PROMPT},
                    {"role": "user", "content": content_parts},
                ],
            },
            timeout=120.0,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return self._parse_response(content)

    def _parse_response(self, content: str) -> ConferenceExtractionResult:
        try:
            clean = content.strip()
            if clean.startswith("```"):
                clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", clean, flags=re.DOTALL)
            parsed = json.loads(clean)
        except (json.JSONDecodeError, ValueError):
            return ConferenceExtractionResult(speakers=[], sponsors=[])

        speakers = [
            SpeakerResult(
                full_name=item["full_name"],
                first_name=None,
                last_name=None,
                title=item.get("title") or None,
                company=item.get("company") or None,
                regalia_raw=item.get("title") or None,
                confidence=92,
                needs_review=False,
            )
            for item in parsed.get("speakers", [])
            if item.get("full_name")
        ]
        sponsors = [
            SponsorResult(
                name=item["name"],
                category=item.get("category") or None,
                confidence=90,
                needs_review=False,
            )
            for item in parsed.get("sponsors", [])
            if item.get("name")
        ]
        return ConferenceExtractionResult(speakers=speakers, sponsors=sponsors)
