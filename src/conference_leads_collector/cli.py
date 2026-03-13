from __future__ import annotations

import argparse
import sys

import uvicorn

from conference_leads_collector.config import AppSettings
from conference_leads_collector.services.tenchat import discover_tenchat_profiles
from conference_leads_collector.services.worker import process_next_job
from conference_leads_collector.storage.db import create_engine, create_schema, session_scope
from conference_leads_collector.storage.repositories import ConferenceSourceRepository, JobRepository
from conference_leads_collector.web.app import create_app


def cmd_init_db(settings: AppSettings) -> int:
    engine = create_engine(settings.database_url)
    create_schema(engine)
    return 0


def cmd_import_seeds(settings: AppSettings, seed_file: str) -> int:
    engine = create_engine(settings.database_url)
    create_schema(engine)
    urls = [line.strip() for line in open(seed_file, encoding="utf-8") if line.strip()]
    with session_scope(engine) as session:
        sources_repo = ConferenceSourceRepository(session)
        jobs_repo = JobRepository(session)
        result = sources_repo.import_seed_urls(urls)
        for source in sources_repo.list_sources_by_urls(urls):
            if source.status == "pending":
                jobs_repo.enqueue_crawl(source.id)
    print(result)
    return 0


def cmd_run_worker(settings: AppSettings, once: bool) -> int:
    engine = create_engine(settings.database_url)
    create_schema(engine)
    if once:
        processed = process_next_job(engine)
        print({"processed": processed})
        return 0

    while True:
        processed = process_next_job(engine)
        if not processed:
            break
    return 0


def cmd_discover_tenchat(settings: AppSettings, queries: list[str]) -> int:
    engine = create_engine(settings.database_url)
    create_schema(engine)
    profiles_found = discover_tenchat_profiles(engine, queries)
    print({"profiles_found": profiles_found})
    return 0


def cmd_web(settings: AppSettings) -> int:
    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Conference Leads Collector")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db")

    import_parser = subparsers.add_parser("import-seeds")
    import_parser.add_argument("seed_file")

    worker_parser = subparsers.add_parser("run-worker")
    worker_parser.add_argument("--once", action="store_true")

    tenchat_parser = subparsers.add_parser("discover-tenchat")
    tenchat_parser.add_argument("queries", nargs="+")

    subparsers.add_parser("web")

    args = parser.parse_args(argv)
    settings = AppSettings.from_env()

    if args.command == "init-db":
        return cmd_init_db(settings)
    if args.command == "import-seeds":
        return cmd_import_seeds(settings, args.seed_file)
    if args.command == "run-worker":
        return cmd_run_worker(settings, args.once)
    if args.command == "discover-tenchat":
        return cmd_discover_tenchat(settings, args.queries)
    if args.command == "web":
        return cmd_web(settings)

    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
