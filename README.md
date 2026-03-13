# Conference Leads Collector

Local service for collecting:
- conference speakers;
- conference sponsors/partners;
- public TenChat profiles for marketing leadership roles.

## Stack

- FastAPI + Jinja2 admin
- SQLAlchemy
- PostgreSQL
- Redis
- httpx + BeautifulSoup + Trafilatura

## Quick Start

1. Copy env:

```bash
cp .env.example .env
```

2. Start infrastructure:

```bash
docker compose up -d
```

3. Create virtualenv and install:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

4. Initialize DB:

```bash
set -a && source .env && set +a
.venv/bin/python -m conference_leads_collector.cli init-db
```

5. Import seed conferences:

```bash
set -a && source .env && set +a
.venv/bin/python -m conference_leads_collector.cli import-seeds seeds/conferences.txt
```

6. Run worker:

```bash
set -a && source .env && set +a
.venv/bin/python -m conference_leads_collector.cli run-worker --once
```

7. Start web admin:

```bash
set -a && source .env && set +a
.venv/bin/python -m conference_leads_collector.cli web
```

Open `http://localhost:8080`.

Admin pages require `Authorization: Bearer <JWT>`.
The HTML dashboard stores the token in `localStorage` after the first prompt.

## JWT Example

Generate a temporary token:

```bash
python3 - <<'PY'
import time, jwt, os
secret = os.environ["CLC_ADMIN_JWT_SECRET"]
print(jwt.encode({"sub": "admin", "exp": int(time.time()) + 86400}, secret, algorithm="HS256"))
PY
```

## TenChat Discovery

CLI example:

```bash
set -a && source .env && set +a
.venv/bin/python -m conference_leads_collector.cli discover-tenchat "директор по маркетингу" "head of marketing"
```

## Tests

```bash
.venv/bin/pytest -q
```
