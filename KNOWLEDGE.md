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
- Current admin visual style is a Russian-language monochrome layout with a shared base template and restrained Apple-like spacing/typography instead of raw standalone tables.
- Human-readable action logging is stored in the `activity_events` table and shown on the dashboard as the primary operator feedback layer for imports, worker runs, TenChat discovery, empty queues, and failures.
- Conference crawling is no longer limited to the seed page: the worker now discovers relevant internal pages (`speakers`, `program`, `agenda`, `archive`, `experts`, `committee`, `sessions`, `tracks` and Russian variants), fetches several candidates on the same domain, and keeps the richest extraction result instead of trusting a JS-heavy showcase page.
- Dashboard worker controls must support batch execution (`run-once`, `run 10`, `run all`) because one-job-per-click is unusable for real conference imports.
- Sponsor extraction must aggressively drop navigation/CTA noise (`Спикеры`, `Программа`, emails, generic links, long prose); noise-only pages should fail the job instead of being marked as successfully crawled.
- AI Gateway balance in the dashboard is loaded from Vercel's official `/v1/credits` endpoint via `CLC_AI_GATEWAY_API_KEY`; that API exposes `balance` and `total_used`, but not a true current-month spend figure.
- Conference extraction can now use AI cleanup via `CLC_AI_GATEWAY_API_KEY` and `CLC_AI_GATEWAY_MODEL`: the worker sends the selected page text plus heuristic candidates to the chat-completions API and prefers the refined result when it is at least as rich as the heuristic result.
- Vercel AI Gateway chat-completions for this project should not use `response_format`; the gateway accepted the request only after falling back to plain prompt-enforced JSON.
- Conference pipeline is now AI-first when the gateway key is present: the worker fetches the seed page plus candidate pages and lets the model extract speakers/sponsors across the page set before falling back to heuristics.
- `AiConferenceRefiner` must not swallow gateway/JSON errors internally; worker-level logging/fallback depends on those exceptions reaching `process_next_job`, otherwise AI quietly degrades to heuristics with no operator signal.
- CLI worker execution must pass `settings` into `process_next_job`; otherwise production runs started via `python -m conference_leads_collector.cli run-worker` silently disable AI even when `CLC_AI_GATEWAY_API_KEY` is present in the environment.
- AI failures during conference extraction should never abort the whole crawl path anymore: the worker logs a human-readable `AI недоступен...` event and falls back to heuristics/cleanup instead of marking the source as broken immediately.
- Final conference entities must be sanitized before saving regardless of origin (`AI` or heuristics): duplicate names are removed, obvious sponsor noise (`Архив`, `Тарифы`, `Партнерам`, `Забронировать стенд`, etc.) is dropped, and invalid speaker names like CTA labels are filtered out.
- `sources` should stay overview-only; detailed work moved to separate `/speakers` and `/sponsors` pages plus CSV exports, otherwise the UI overflows and becomes unusable.
- Primary operator exports should be `.xlsx`, not only CSV; the service now renders Excel files directly from the current database state for speakers, sponsors, and TenChat.
- TenChat discovery cannot rely on DuckDuckGo HTML anymore because it often returns an anti-bot challenge; Bing RSS is the current public fallback, and profile parsing should support direct profile URLs as well as `tenchat.ru/post/...` pages via schema.org metadata and the public subscriber counter.
- TenChat discovery should treat direct `tenchat.ru/...` values in the query box as explicit profile URLs and fetch them directly instead of sending them through public search.
