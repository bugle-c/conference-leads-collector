FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

FROM base AS builder
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
    && /opt/venv/bin/pip install .

FROM base AS runner
WORKDIR /app

ENV PATH="/opt/venv/bin:$PATH"
ENV CLC_HOST="0.0.0.0"
ENV CLC_PORT="3000"

COPY --from=builder /opt/venv /opt/venv
COPY src ./src
COPY seeds ./seeds
COPY README.md pyproject.toml ./

RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 3000

CMD ["python", "-m", "conference_leads_collector.cli", "web"]
