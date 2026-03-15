from pathlib import Path

from conference_leads_collector.storage.db import create_engine, create_schema, session_scope
from conference_leads_collector.storage.repositories import ConferenceSourceRepository, JobRepository


def test_import_seed_urls_inserts_unique_normalized_sources(tmp_path: Path) -> None:
    db_path = tmp_path / "collector.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    create_schema(engine)

    with session_scope(engine) as session:
        repo = ConferenceSourceRepository(session)
        result = repo.import_seed_urls(
            [
                "aisummit.ru",
                "https://aisummit.ru/",
                " www.mergeconf.ru ",
                "https://www.mergeconf.ru",
            ]
        )

        sources = repo.list_sources()

    assert result == {"inserted": 2, "skipped": 2}
    assert [source.seed_url for source in sources] == [
        "https://aisummit.ru",
        "https://www.mergeconf.ru",
    ]
    assert all(source.status == "pending" for source in sources)


def test_import_seed_urls_keeps_existing_records_and_adds_new_ones(tmp_path: Path) -> None:
    db_path = tmp_path / "collector.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    create_schema(engine)

    with session_scope(engine) as session:
        repo = ConferenceSourceRepository(session)
        repo.import_seed_urls(["https://aisummit.ru"])

    with session_scope(engine) as session:
        repo = ConferenceSourceRepository(session)
        result = repo.import_seed_urls(["https://aisummit.ru", "https://productsense.io"])
        sources = repo.list_sources()

    assert result == {"inserted": 1, "skipped": 1}
    assert [source.seed_url for source in sources] == [
        "https://aisummit.ru",
        "https://productsense.io",
    ]


def test_reconcile_statuses_marks_stale_pending_source_as_failed(tmp_path: Path) -> None:
    db_path = tmp_path / "collector.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    create_schema(engine)

    with session_scope(engine) as session:
        sources = ConferenceSourceRepository(session)
        jobs = JobRepository(session)
        sources.import_seed_urls(["https://aisummit.ru"])
        source = sources.list_sources()[0]
        job = jobs.enqueue_crawl(source.id)
        jobs.mark_failed(job, "No high-quality entities found")
        source.status = "pending"
        sources.reconcile_statuses()
        repaired = sources.list_sources()[0]

    assert repaired.status == "failed"
    assert repaired.notes == "No high-quality entities found"


def test_reconcile_statuses_marks_stale_pending_source_as_crawled(tmp_path: Path) -> None:
    db_path = tmp_path / "collector.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    create_schema(engine)

    with session_scope(engine) as session:
        sources = ConferenceSourceRepository(session)
        jobs = JobRepository(session)
        sources.import_seed_urls(["https://aisummit.ru"])
        source = sources.list_sources()[0]
        job = jobs.enqueue_crawl(source.id)
        sources.mark_crawled(
            source.id,
            "https://aisummit.ru",
            200,
            "<html></html>",
            [{"full_name": "Jane Smith", "first_name": "Jane", "last_name": "Smith"}],
            [],
        )
        jobs.mark_done(job)
        source.status = "pending"
        sources.reconcile_statuses()
        repaired = sources.list_sources()[0]

    assert repaired.status == "crawled"
