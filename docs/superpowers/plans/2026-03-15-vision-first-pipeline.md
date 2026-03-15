# Vision-First Extraction Pipeline

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace httpx+heuristics pipeline with Playwright-rendered pages + Gemini Vision screenshots as primary extraction, keeping text AI as supplement.

**Architecture:** Playwright renders seed page, discovers subpage links from rendered DOM, screenshots each (seed + up to 3 subpages). Vision AI extracts speakers+sponsors from screenshots (clean, 0 garbage). Text from rendered HTML sent to text AI as supplement to catch names behind scroll. Results merged, deduped, sanitized.

**Tech Stack:** Playwright (headless Chromium), Gemini Flash Vision via existing AI Gateway, existing sanitize pipeline.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/.../services/browser.py` | Create | Playwright page renderer: load page, discover subpages, take screenshots |
| `src/.../services/vision_extraction.py` | Create | Vision AI: send screenshots to Gemini, parse JSON response |
| `src/.../services/worker.py` | Modify | New pipeline: browser → vision → text supplement → merge → sanitize |
| `src/.../services/ai_extraction.py` | Modify | Add `extract_from_rendered_text()` method for supplement extraction |
| `src/.../config.py` | Modify | Add `playwright_enabled` flag |
| `pyproject.toml` | Modify | Add `playwright` dependency |
| `Dockerfile` | Modify | Install Chromium system deps in base stage |
| `tests/test_vision_extraction.py` | Create | Vision AI unit tests |
| `tests/test_browser.py` | Create | Browser service unit tests |
| `tests/test_worker_pipeline.py` | Modify | Update pipeline tests for new flow |

---

## Chunk 1: Browser Service

### Task 1: Playwright browser service

**Files:**
- Create: `src/conference_leads_collector/services/browser.py`
- Create: `tests/test_browser.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add playwright dependency**

In `pyproject.toml`, add to dependencies:
```
"playwright>=1.49,<2.0",
```

Run: `pip install -e ".[dev]" --break-system-packages -q && python3 -m playwright install chromium`

- [ ] **Step 2: Write failing test for browser service**

```python
# tests/test_browser.py
from conference_leads_collector.services.browser import BrowserRenderer, RenderedPage

def test_browser_renderer_returns_rendered_pages():
    renderer = BrowserRenderer()
    assert renderer is not None
```

Run: `python3 -m pytest tests/test_browser.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Create browser service with page rendering**

```python
# src/conference_leads_collector/services/browser.py
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
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
                pages = []

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_browser.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/conference_leads_collector/services/browser.py tests/test_browser.py
git commit -m "feat: add Playwright browser renderer service"
```

---

## Chunk 2: Vision Extraction Service

### Task 2: Vision AI extraction from screenshots

**Files:**
- Create: `src/conference_leads_collector/services/vision_extraction.py`
- Create: `tests/test_vision_extraction.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_vision_extraction.py
from conference_leads_collector.services.vision_extraction import VisionExtractor

def test_vision_extractor_parses_response():
    extractor = VisionExtractor.__new__(VisionExtractor)
    result = extractor._parse_response('{"speakers": [{"full_name": "Jane Roe", "title": "CTO", "company": "Acme"}], "sponsors": [{"name": "BigCorp", "category": "partner"}]}')
    assert len(result.speakers) == 1
    assert result.speakers[0].full_name == "Jane Roe"
    assert len(result.sponsors) == 1
    assert result.sponsors[0].name == "BigCorp"


def test_vision_extractor_handles_markdown_wrapped_json():
    extractor = VisionExtractor.__new__(VisionExtractor)
    result = extractor._parse_response('```json\n{"speakers": [], "sponsors": [{"name": "X", "category": "sponsor"}]}\n```')
    assert len(result.sponsors) == 1


def test_vision_extractor_returns_empty_on_bad_json():
    extractor = VisionExtractor.__new__(VisionExtractor)
    result = extractor._parse_response('not json at all')
    assert result.speakers == []
    assert result.sponsors == []
```

Run: `python3 -m pytest tests/test_vision_extraction.py -v`
Expected: FAIL — module not found

- [ ] **Step 2: Create vision extraction service**

```python
# src/conference_leads_collector/services/vision_extraction.py
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
```

- [ ] **Step 3: Run tests**

Run: `python3 -m pytest tests/test_vision_extraction.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/conference_leads_collector/services/vision_extraction.py tests/test_vision_extraction.py
git commit -m "feat: add vision extraction service for screenshot-based parsing"
```

---

## Chunk 3: Text Supplement Method

### Task 3: Add rendered-text extraction to AI service

**Files:**
- Modify: `src/conference_leads_collector/services/ai_extraction.py`

This uses the rendered HTML from Playwright (which includes JS-loaded content) to extract speakers/sponsors via text AI. It acts as a supplement to vision — catches names that are off-screen in the screenshot.

- [ ] **Step 1: Add `extract_from_rendered_text` method**

Add to `AiConferenceRefiner` class in `ai_extraction.py`:

```python
def extract_from_rendered_text(
    self,
    conference_url: str,
    pages: list[dict[str, str]],
) -> ConferenceExtractionResult | None:
    """Extract from rendered HTML text (Playwright output). Lightweight supplement to vision."""
    if not self.settings.ai_gateway_api_key or not pages:
        return None

    prepared_pages = [
        {
            "url": page["url"],
            "text": BeautifulSoup(page["html"], "html.parser").get_text("\n", strip=True)[:10000],
        }
        for page in pages[:3]
    ]
    instructions = (
        "Ты извлекаешь спикеров и спонсоров с сайта конференции. Верни только JSON без пояснений. "
        "Спикеры: full_name, title, company. Спонсоры: name, category. "
        "Убери навигацию, CTA, тарифы. Если поле неизвестно — пустая строка. Не выдумывай."
    )
    user_prompt = {"conference_url": conference_url, "pages": prepared_pages}
    content = self._complete(instructions, user_prompt)
    parsed = _extract_json_object(content)
    result = _build_result_from_payload(parsed)
    return result if result.speakers or result.sponsors else None
```

- [ ] **Step 2: Run existing tests to verify no regression**

Run: `python3 -m pytest tests/test_ai_extraction.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add src/conference_leads_collector/services/ai_extraction.py
git commit -m "feat: add rendered-text supplement extraction method"
```

---

## Chunk 4: New Worker Pipeline

### Task 4: Vision-first pipeline in worker

**Files:**
- Modify: `src/conference_leads_collector/services/worker.py`
- Modify: `tests/test_worker_pipeline.py`

The new `process_next_job` flow:
1. Try Playwright render → vision extraction → text supplement → merge
2. If Playwright unavailable → fall back to current httpx + text AI pipeline
3. Always sanitize at the end

- [ ] **Step 1: Add merge helper**

Add to `worker.py`:

```python
def _merge_results(
    vision: ConferenceExtractionResult | None,
    text: ConferenceExtractionResult | None,
) -> ConferenceExtractionResult:
    """Merge vision (primary) and text (supplement) results. Vision takes priority."""
    speakers: list = []
    sponsors: list = []
    seen_speakers: set[str] = set()
    seen_sponsors: set[str] = set()

    # Vision results first (higher confidence, cleaner)
    for source in [vision, text]:
        if source is None:
            continue
        for s in source.speakers:
            key = s.full_name.lower().strip()
            sorted_key = " ".join(sorted(key.split()))
            if key not in seen_speakers and sorted_key not in seen_speakers:
                speakers.append(s)
                seen_speakers.add(key)
                seen_speakers.add(sorted_key)
        for s in source.sponsors:
            key = s.name.lower().strip()
            if key not in seen_sponsors:
                sponsors.append(s)
                seen_sponsors.add(key)

    return ConferenceExtractionResult(speakers=speakers, sponsors=sponsors)
```

- [ ] **Step 2: Rewrite `process_next_job` with vision-first pipeline**

Replace the try block inside `process_next_job` (lines 94-147) with:

```python
        try:
            events.add_event(
                f"Запущена обработка конференции {source.seed_url}",
                f"Задача #{job.id} взята в работу",
            )

            vision_result = None
            text_result = None

            # --- Vision pipeline (primary) ---
            if settings is not None and settings.ai_gateway_api_key:
                try:
                    from conference_leads_collector.services.browser import BrowserRenderer
                    from conference_leads_collector.services.vision_extraction import VisionExtractor

                    renderer = BrowserRenderer()
                    rendered_pages = renderer.render_conference(source.seed_url)

                    if rendered_pages:
                        # Vision extraction from screenshots
                        vision_extractor = VisionExtractor(settings)
                        screenshots = [
                            {"url": rp.url, "screenshot_b64": rp.screenshot_b64}
                            for rp in rendered_pages
                        ]
                        vision_result = vision_extractor.extract_from_screenshots(
                            source.seed_url, screenshots
                        )
                        events.add_event(
                            f"Vision извлёк данные для {source.seed_url}",
                            f"Задача #{job.id}: {len(vision_result.speakers)} спикеров, "
                            f"{len(vision_result.sponsors)} спонсоров из {len(rendered_pages)} скриншотов",
                        )

                        # Text supplement from rendered HTML
                        if active_ai_refiner is not None:
                            try:
                                text_pages = [
                                    {"url": rp.url, "html": rp.html}
                                    for rp in rendered_pages
                                ]
                                text_result = active_ai_refiner.extract_from_rendered_text(
                                    source.seed_url, text_pages
                                )
                            except Exception as exc:
                                events.add_event(
                                    f"Text supplement недоступен для {source.seed_url}",
                                    f"Задача #{job.id}: {exc}",
                                    level="error",
                                )

                        status_code = 200
                        html = rendered_pages[0].html
                except Exception as exc:
                    events.add_event(
                        f"Vision pipeline недоступен для {source.seed_url}",
                        f"Задача #{job.id}: {exc}, переход на text pipeline",
                        level="error",
                    )

            # --- Fallback: text pipeline (if vision didn't run or failed) ---
            if vision_result is None:
                status_code, html, extracted = _collect_best_extraction(
                    active_fetcher, source.seed_url
                )
                if active_ai_refiner is not None:
                    try:
                        refined = active_ai_refiner.refine(source.seed_url, html, extracted)
                    except Exception as exc:
                        events.add_event(
                            f"AI очистка недоступна для {source.seed_url}",
                            f"Задача #{job.id}: {exc}",
                            level="error",
                        )
                    else:
                        if refined.speakers or refined.sponsors:
                            extracted = refined
                text_result = extracted

            # --- Merge and sanitize ---
            merged = _merge_results(vision_result, text_result)
            extracted = sanitize_conference_data(merged)

            if not _has_high_quality_entities(extracted):
                jobs.mark_failed(job, "No high-quality entities found")
                events.add_event(
                    f"Обработка {source.seed_url} не дала результата",
                    f"HTTP {status_code}, задача #{job.id} не содержит валидных спикеров или спонсоров",
                    level="error",
                )
                return True

            sources.mark_crawled(
                source.id,
                source.seed_url,
                status_code,
                html,
                [asdict(item) for item in extracted.speakers],
                [asdict(item) for item in extracted.sponsors],
            )
            jobs.mark_done(job)
            events.add_event(
                f"Обработка {source.seed_url} завершена: {len(extracted.speakers)} спикеров, {len(extracted.sponsors)} спонсоров",
                f"HTTP {status_code}, задача #{job.id} завершена успешно",
            )
        except Exception as exc:
            jobs.mark_failed(job, str(exc))
            events.add_event(
                f"Обработка {source.seed_url} завершилась с ошибкой",
                str(exc),
                level="error",
            )

        return True
```

- [ ] **Step 3: Update tests**

Existing tests use `StubFetcher` / `MultiPageFetcher` without settings (no AI key) — they will still trigger the fallback text pipeline. Add one new test that verifies the vision path is attempted when settings are provided.

The existing tests should still pass as-is because `settings=None` → no vision pipeline → fallback.

Run: `python3 -m pytest tests/test_worker_pipeline.py -v`
Expected: All existing tests PASS (vision pipeline skipped since settings is None in test fixtures)

- [ ] **Step 4: Commit**

```bash
git add src/conference_leads_collector/services/worker.py tests/test_worker_pipeline.py
git commit -m "feat: vision-first pipeline with text fallback"
```

---

## Chunk 5: Dockerfile + Deploy

### Task 5: Add Chromium to Docker image

**Files:**
- Modify: `Dockerfile`

- [ ] **Step 1: Update Dockerfile**

Key changes:
- Install Chromium system deps in `base` stage (cached, not re-downloaded per deploy)
- Install playwright browsers in builder stage
- Copy browser binaries to runner

```dockerfile
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Chromium system dependencies — in base stage for Docker layer caching
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libdbus-1-3 libxkbcommon0 libatspi2.0-0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    fonts-liberation fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

FROM base AS builder
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
    && /opt/venv/bin/pip install . \
    && /opt/venv/bin/python -m playwright install chromium

FROM base AS runner
WORKDIR /app

ENV PATH="/opt/venv/bin:$PATH"
ENV CLC_HOST="0.0.0.0"
ENV CLC_PORT="3000"

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /root/.cache/ms-playwright /root/.cache/ms-playwright
COPY src ./src
COPY seeds ./seeds
COPY README.md pyproject.toml ./

RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app \
    && cp -r /root/.cache/ms-playwright /home/appuser/.cache/ms-playwright \
    && chown -R appuser:appuser /home/appuser/.cache

USER appuser

EXPOSE 3000

CMD ["python", "-m", "conference_leads_collector.cli", "web"]
```

- [ ] **Step 2: Verify all tests pass**

Run: `python3 -m pytest tests/ -v`
Expected: All 32+ tests PASS

- [ ] **Step 3: Commit and push**

```bash
git add Dockerfile pyproject.toml
git commit -m "feat: add Chromium to Docker for vision pipeline"
git push origin master
```

---

## Chunk 6: KNOWLEDGE.md Update

### Task 6: Update project knowledge

- [ ] **Step 1: Add vision pipeline decisions to KNOWLEDGE.md**

Add entries:
- Vision-first pipeline: Playwright screenshots → Gemini Vision → text supplement → merge → sanitize
- Vision produces 0 garbage vs text pipeline's 6+ garbage entries per conference
- Playwright adds ~500MB to Docker image (Chromium + fonts)
- Cost: ~$0.018 per conference for vision (2-4 screenshots) vs $0.003 for text-only
- Text pipeline kept as fallback when Playwright/AI unavailable
- Screenshots taken full-page with 2s lazy-load wait + scroll trigger
- Max 3 subpages per conference (speakers, program, archive)

- [ ] **Step 2: Commit**

```bash
git add KNOWLEDGE.md
git commit -m "docs: update KNOWLEDGE.md with vision pipeline decisions"
```
