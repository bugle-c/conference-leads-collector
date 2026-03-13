from pathlib import Path

from conference_leads_collector.extractors.conferences import ConferenceExtractionResult, SpeakerResult, SponsorResult
from conference_leads_collector.services.worker import process_next_job
from conference_leads_collector.storage.db import create_engine, create_schema, session_scope
from conference_leads_collector.storage.repositories import ActivityEventRepository, ConferenceSourceRepository, JobRepository


HTML = """
<html>
  <body>
    <section>
      <h2>Speakers</h2>
      <article class="speaker-card">
        <h3>John Doe</h3>
        <p>Chief Marketing Officer, Acme Inc</p>
      </article>
    </section>
    <section>
      <h2>Partners</h2>
      <div class="partner-card">
        <img alt="Acme Cloud" src="/logo.png" />
      </div>
    </section>
  </body>
</html>
"""


class StubFetcher:
    def fetch(self, url: str):
        return 200, HTML


class MultiPageFetcher:
    def fetch(self, url: str):
        pages = {
            "https://example.com/conf": (
                200,
                """
                <html><body>
                  <a href="/speakers">Спикеры</a>
                  <a href="/program">Программа</a>
                </body></html>
                """,
            ),
            "https://example.com/speakers": (
                200,
                """
                <html><body>
                  <section><h2>Спикеры</h2><div>Карточки загружаются позже</div></section>
                </body></html>
                """,
            ),
            "https://example.com/program": (
                200,
                """
                <html><body>
                  <section>
                    <h2>Программа</h2>
                    <article class="speaker-card">
                      <h3>Jane Roe</h3>
                      <p>VP Marketing, Bright AI</p>
                    </article>
                  </section>
                  <section>
                    <h2>Партнеры</h2>
                    <div class="partner-card"><img alt="North Star AI" src="/logo.png" /></div>
                  </section>
                </body></html>
                """,
            ),
        }
        return pages[url]


def test_process_next_job_extracts_entities_and_marks_job_done(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)

    with session_scope(engine) as session:
        sources = ConferenceSourceRepository(session)
        jobs = JobRepository(session)
        sources.import_seed_urls(["https://example.com/conf"])
        source = sources.list_sources()[0]
        jobs.enqueue_crawl(source.id)

    processed = process_next_job(engine, fetcher=StubFetcher())

    assert processed is True

    with session_scope(engine) as session:
        sources = ConferenceSourceRepository(session)
        jobs = JobRepository(session)
        source = sources.list_sources()[0]
        job = jobs.list_jobs()[0]

        assert source.status == "crawled"
        assert job.status == "done"
        assert len(source.discovered_pages) == 1
        assert len(source.speakers) == 1
        assert source.speakers[0].full_name == "John Doe"
        assert source.sponsors[0].name == "Acme Cloud"


def test_process_next_job_discovers_richer_internal_pages(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)

    with session_scope(engine) as session:
        sources = ConferenceSourceRepository(session)
        jobs = JobRepository(session)
        sources.import_seed_urls(["https://example.com/conf"])
        source = sources.list_sources()[0]
        jobs.enqueue_crawl(source.id)

    processed = process_next_job(engine, fetcher=MultiPageFetcher())

    assert processed is True

    with session_scope(engine) as session:
        sources = ConferenceSourceRepository(session)
        source = sources.list_sources()[0]

        assert source.status == "crawled"
        assert [speaker.full_name for speaker in source.speakers] == ["Jane Roe"]
        assert [sponsor.name for sponsor in source.sponsors] == ["North Star AI"]


class NoiseOnlyFetcher:
    def fetch(self, url: str):
        return 200, """
        <html><body>
          <section>
            <h2>Спонсоры</h2>
            <a href="/speakers">Спикеры</a>
            <a href="/program">Программа</a>
            <a href="/tickets">Купить билет</a>
          </section>
        </body></html>
        """


def test_process_next_job_marks_noise_only_pages_as_failed(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)

    with session_scope(engine) as session:
        sources = ConferenceSourceRepository(session)
        jobs = JobRepository(session)
        sources.import_seed_urls(["https://example.com/noise"])
        source = sources.list_sources()[0]
        jobs.enqueue_crawl(source.id)

    processed = process_next_job(engine, fetcher=NoiseOnlyFetcher())

    assert processed is True

    with session_scope(engine) as session:
        sources = ConferenceSourceRepository(session)
        jobs = JobRepository(session)
        source = sources.list_sources()[0]
        job = jobs.list_jobs()[0]

        assert source.status == "pending"
        assert source.speakers == []
        assert source.sponsors == []
        assert job.status == "failed"


class RefiningAi:
    def refine(self, conference_url: str, html: str, extracted):
        extracted.sponsors = []
        return extracted

    def extract_from_pages(self, conference_url: str, pages):
        return None


def test_process_next_job_can_use_ai_refiner_to_clean_results(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)

    with session_scope(engine) as session:
        sources = ConferenceSourceRepository(session)
        jobs = JobRepository(session)
        sources.import_seed_urls(["https://example.com/conf"])
        source = sources.list_sources()[0]
        jobs.enqueue_crawl(source.id)

    processed = process_next_job(engine, fetcher=StubFetcher(), ai_refiner=RefiningAi())

    assert processed is True

    with session_scope(engine) as session:
        sources = ConferenceSourceRepository(session)
        source = sources.list_sources()[0]

        assert source.status == "crawled"
        assert [speaker.full_name for speaker in source.speakers] == ["John Doe"]
        assert source.sponsors == []


class AiFirstFetcher:
    def fetch(self, url: str):
        pages = {
            "https://example.com/conf": (
                200,
                """
                <html><body>
                  <a href="/program">Программа</a>
                </body></html>
                """,
            ),
            "https://example.com/program": (
                200,
                """
                <html><body>
                  <section><h2>Программа</h2><div>opaque source text</div></section>
                </body></html>
                """,
            ),
        }
        return pages[url]


class AiFirstRefiner:
    def extract_from_pages(self, conference_url: str, pages):
        return ConferenceExtractionResult(
            speakers=[
                SpeakerResult(
                    full_name="Jane Roe",
                    first_name="Jane",
                    last_name="Roe",
                    title="CMO",
                    company="Bright AI",
                    regalia_raw="CMO, Bright AI",
                )
            ],
            sponsors=[SponsorResult(name="North Star AI")],
        )

    def refine(self, conference_url: str, html: str, extracted):
        return extracted


def test_process_next_job_prefers_ai_first_extraction(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)

    with session_scope(engine) as session:
        sources = ConferenceSourceRepository(session)
        jobs = JobRepository(session)
        sources.import_seed_urls(["https://example.com/conf"])
        source = sources.list_sources()[0]
        jobs.enqueue_crawl(source.id)

    processed = process_next_job(engine, fetcher=AiFirstFetcher(), ai_refiner=AiFirstRefiner())

    assert processed is True

    with session_scope(engine) as session:
        source = ConferenceSourceRepository(session).list_sources()[0]
        assert [speaker.full_name for speaker in source.speakers] == ["Jane Roe"]
        assert [sponsor.name for sponsor in source.sponsors] == ["North Star AI"]


class NoisyAiFirstRefiner:
    def extract_from_pages(self, conference_url: str, pages):
        return ConferenceExtractionResult(
            speakers=[
                SpeakerResult(
                    full_name="Купить билет",
                    first_name=None,
                    last_name=None,
                    title="",
                    company="",
                    regalia_raw="",
                ),
                SpeakerResult(
                    full_name="Jane Roe",
                    first_name="Jane",
                    last_name="Roe",
                    title="CMO",
                    company="Bright AI",
                    regalia_raw="CMO, Bright AI",
                ),
            ],
            sponsors=[
                SponsorResult(name="Архив"),
                SponsorResult(name="North Star AI"),
            ],
        )

    def refine(self, conference_url: str, html: str, extracted):
        return extracted


def test_process_next_job_sanitizes_ai_entities_before_saving(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)

    with session_scope(engine) as session:
        sources = ConferenceSourceRepository(session)
        jobs = JobRepository(session)
        sources.import_seed_urls(["https://example.com/conf"])
        source = sources.list_sources()[0]
        jobs.enqueue_crawl(source.id)

    processed = process_next_job(engine, fetcher=AiFirstFetcher(), ai_refiner=NoisyAiFirstRefiner())

    assert processed is True

    with session_scope(engine) as session:
        source = ConferenceSourceRepository(session).list_sources()[0]
        assert [speaker.full_name for speaker in source.speakers] == ["Jane Roe"]
        assert [sponsor.name for sponsor in source.sponsors] == ["North Star AI"]


class BrokenAiRefiner:
    def extract_from_pages(self, conference_url: str, pages):
        raise RuntimeError("gateway timeout")

    def refine(self, conference_url: str, html: str, extracted):
        return extracted


def test_process_next_job_logs_ai_failure_and_falls_back_to_heuristics(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)

    with session_scope(engine) as session:
        sources = ConferenceSourceRepository(session)
        jobs = JobRepository(session)
        sources.import_seed_urls(["https://example.com/conf"])
        source = sources.list_sources()[0]
        jobs.enqueue_crawl(source.id)

    processed = process_next_job(engine, fetcher=MultiPageFetcher(), ai_refiner=BrokenAiRefiner())

    assert processed is True

    with session_scope(engine) as session:
        source = ConferenceSourceRepository(session).list_sources()[0]
        events = ActivityEventRepository(session).list_recent(limit=10)

        assert [speaker.full_name for speaker in source.speakers] == ["Jane Roe"]
        assert any("AI недоступен" in event.title for event in events)
