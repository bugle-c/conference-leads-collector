# KNOWLEDGE.md

## Architecture

- Standalone local service for conference speaker/sponsor extraction and public TenChat profile discovery.
- Planned runtime split: `web`, `worker`, `scheduler`.
- Stack target: FastAPI, PostgreSQL, Redis, Playwright fallback, server-rendered HTML admin.

## Key Constraints

- TenChat scraping is public-only: no login, no captcha solving.
- Admin/API access is protected by JWT token, no full auth subsystem.
- Local/manual deployment only; no Dokploy integration.

## Decisions

- Start with a production-oriented project layout from day one instead of an MVP-only structure.
- Keep raw evidence and normalized entities separately; confidence and review flags are first-class.
- Use resilient per-page/per-profile jobs so partial failures do not stop full runs.
- For FastAPI tests in this environment, avoid `TestClient`; `httpx.AsyncClient + ASGITransport` is stable here.
- Prefer `async def` route handlers for the initial app layer; sync handlers caused hangs through the anyio threadpool path during ASGI test requests.
- Normalize `postgresql://` env URLs to `postgresql+psycopg://` so SQLAlchemy uses psycopg3 consistently.
- Seed import enqueues conference crawl jobs immediately; repeated imports must not create duplicate pending/running crawl jobs.
- Current admin is intentionally server-rendered HTML plus small inline JS actions for seed import, worker run-once, and TenChat discovery.
- Dokploy build image should stay on plain `python:3.12-slim` without `apt-get install build-essential curl`; current dependency set uses binary wheels and does not need system toolchains.
- `httpx` is a runtime dependency because TenChat discovery/parsing imports it from the application package; keeping it in `dev` breaks container startup.
- In Dokploy containers, templates are not available inside the installed wheel by default; the web app must fall back to `/app/src/conference_leads_collector/web/templates`, which is copied into the runtime image.
- Admin navigation should preserve the `token` query parameter between pages; the current UI relies on query-token access for direct browser navigation, while API calls still use `Authorization: Bearer`.
- Browser-triggered admin API actions must also accept `?token=` and the dashboard should append it to fetch URLs as a fallback; relying on `Authorization` alone is brittle behind some browser/proxy setups and manifests as client-side `Failed to fetch`.
- Dashboard operator actions should give immediate UI feedback: toast notifications on click plus temporary button disabling/loading labels. The result box remains the detailed payload/log view, not the primary click acknowledgement.
- Async admin routes must not execute long synchronous work directly in the event-loop thread. `run-once`, `run-batch`, `requeue`, and `tenchat/discover` should hop into a dedicated worker thread; otherwise the whole web UI appears frozen while one task runs.
- Dashboard home layout should stay server-rendered and monochrome, but use stable grid sections (`hero`, actions, exports, result, activity) with mobile-safe wrapping; free-form button rows and oversized raw numbers caused panels to visually collide.
- Current admin visual style is a Russian-language monochrome layout with a shared base template and restrained Apple-like spacing/typography instead of raw standalone tables.
- Human-readable action logging is stored in the `activity_events` table and shown on the dashboard as the primary operator feedback layer for imports, worker runs, TenChat discovery, empty queues, and failures.
- Conference crawling is no longer limited to the seed page: the worker now discovers relevant internal pages (`speakers`, `program`, `agenda`, `archive`, `experts`, `committee`, `sessions`, `tracks` and Russian variants), fetches several candidates on the same domain, and keeps the richest extraction result instead of trusting a JS-heavy showcase page.
- Dashboard worker controls must support batch execution (`run-once`, `run 10`, `run all`) because one-job-per-click is unusable for real conference imports.
- Sponsor extraction must aggressively drop navigation/CTA noise (`Спикеры`, `Программа`, emails, generic links, long prose); noise-only pages should fail the job instead of being marked as successfully crawled.
- AI Gateway balance in the dashboard is loaded from Vercel's official `/v1/credits` endpoint via `CLC_AI_GATEWAY_API_KEY`; that API exposes `balance` and `total_used`, but not a true current-month spend figure.
- Conference extraction can now use AI cleanup via `CLC_AI_GATEWAY_API_KEY` and `CLC_AI_GATEWAY_MODEL`: the worker sends the selected page text plus heuristic candidates to the chat-completions API and prefers the refined result when it is at least as rich as the heuristic result.
- Vercel AI Gateway chat-completions for this project should not use `response_format`; the gateway accepted the request only after falling back to plain prompt-enforced JSON.
- Conference pipeline is heuristics-first: the worker always runs heuristic extraction first, and only calls AI refine when the heuristic result has fewer than 3 speakers. This eliminates the expensive AI-first path and the double AI call pattern. AI context is capped at 2 pages × 10K chars (was 5 × 30K).
- `AiConferenceRefiner` must not swallow gateway/JSON errors internally; worker-level logging/fallback depends on those exceptions reaching `process_next_job`, otherwise AI quietly degrades to heuristics with no operator signal.
- CLI worker execution must pass `settings` into `process_next_job`; otherwise production runs started via `python -m conference_leads_collector.cli run-worker` silently disable AI even when `CLC_AI_GATEWAY_API_KEY` is present in the environment.
- AI failures during conference extraction should never abort the whole crawl path anymore: the worker logs a human-readable `AI недоступен...` event and falls back to heuristics/cleanup instead of marking the source as broken immediately.
- Final conference entities must be sanitized before saving regardless of origin (`AI` or heuristics): duplicate names are removed, obvious sponsor noise (`Архив`, `Тарифы`, `Партнерам`, `Забронировать стенд`, etc.) is dropped, and invalid speaker names like CTA labels are filtered out.
- `sources` should stay overview-only; detailed work moved to separate `/speakers` and `/sponsors` pages plus CSV exports, otherwise the UI overflows and becomes unusable.
- Primary operator exports should be `.xlsx`, not only CSV; the service now renders Excel files directly from the current database state for speakers, sponsors, and TenChat.
- TenChat discovery cannot rely on DuckDuckGo HTML anymore because it often returns an anti-bot challenge; Bing RSS is the current public fallback, and profile parsing should support direct profile URLs as well as `tenchat.ru/post/...` pages via schema.org metadata and the public subscriber counter.
- TenChat discovery should treat direct `tenchat.ru/...` values in the query box as explicit profile URLs and fetch them directly instead of sending them through public search.
- Sponsor img[alt] extraction is now limited to sections with explicit sponsor/partner headings; previously any section/div without a heading would expose img alts as sponsor names, producing garbage.
- Speaker name validation requires minimum 5 characters and has expanded noise filters covering CTA labels, navigation items, and common Russian web UI terms.
- Vision-first pipeline: Playwright screenshots → Gemini Vision → text AI supplement → merge → sanitize. Vision produces 0 garbage vs text pipeline's 6+ garbage entries per conference. Text pipeline kept as automatic fallback when Playwright/AI unavailable.
- When the vision-first pipeline is triggered from async web routes, sync Playwright must run in a separate thread; otherwise production logs show `Playwright Sync API inside the asyncio loop`, vision silently falls back to text-only extraction, and JS-heavy conferences can end with false `No high-quality entities found`.
- Manual queue control is source-driven now: new imports create `pending` sources/jobs automatically, worker transitions sources through `running` → `crawled`/`failed`, and operators can explicitly requeue an existing conference from `/sources` without re-importing the URL.
- Operator-triggered `requeue` must process one job immediately after enqueue; only creating a `pending` job was too opaque in the web UI and looked like the conference was "stuck in queue".
- Jinja `tojson` must not be embedded inside double-quoted HTML event attributes. The `/sources` requeue button broke in production because `onclick="... {{ url|tojson }} ..."` produced invalid markup; use `data-*` attributes plus JS event listeners instead.
- When seed HTML is just a JS shell, worker candidate discovery should also probe `/sitemap.xml` and merge entities across multiple relevant pages (`program`, `archive`, `speakers`, etc.) instead of keeping only a single best page.
- TenChat discovery should store broader marketing profiles and mark out-of-band follower counts for review; hard-dropping everything outside `1000..3000` caused frequent false zero-result runs.
- AI credit values in the dashboard should be compactly rounded to 2 decimals before rendering; raw gateway floats like `22.20375925` make stat cards overflow and reduce readability.
- Playwright adds ~500MB to Docker image (Chromium + system fonts). System deps installed in base stage for layer caching.
- Cost per conference: ~$0.018 for vision (2-4 screenshots) + ~$0.003 for text supplement = ~$0.021 total. Old AI-first was ~$0.05+ (150K chars context).
- Screenshots taken full-page with 2s lazy-load wait + scroll trigger. Max 3 subpages per conference (speakers, program, archive, sponsors).
- Merge strategy: vision results first (higher confidence), text supplement adds missing entries. Dedup by sorted name tokens catches "Иванов Иван" == "Иван Иванов".
- Sponsor extraction via generic section/div requires explicit sponsor/partner heading. Navigation menu-item li elements are filtered out.
- ORG_WORDS expanded with Russian terms (институт, университет, банк, etc.) to reject organizations in speaker names. Job titles (руководитель, директор, head of, chief) also rejected.
