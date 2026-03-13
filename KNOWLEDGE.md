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
