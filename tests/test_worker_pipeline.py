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


class SitemapFetcher:
    def fetch(self, url: str):
        pages = {
            "https://example.com/conf": (
                200,
                """
                <html><body>
                  <a href="/about">О конференции</a>
                </body></html>
                """,
            ),
            "https://example.com/sitemap.xml": (
                200,
                """
                <?xml version="1.0" encoding="UTF-8"?>
                <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                  <url><loc>https://example.com/program</loc></url>
                  <url><loc>https://example.com/archive</loc></url>
                </urlset>
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
                </body></html>
                """,
            ),
            "https://example.com/archive": (
                200,
                """
                <html><body>
                  <section>
                    <h2>Архив</h2>
                    <article class="speaker-card">
                      <h3>Alex Poe</h3>
                      <p>Chief Brand Officer, North AI</p>
                    </article>
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


def test_process_next_job_uses_sitemap_when_seed_page_has_no_useful_links(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)

    with session_scope(engine) as session:
        sources = ConferenceSourceRepository(session)
        jobs = JobRepository(session)
        sources.import_seed_urls(["https://example.com/conf"])
        source = sources.list_sources()[0]
        jobs.enqueue_crawl(source.id)

    processed = process_next_job(engine, fetcher=SitemapFetcher())

    assert processed is True

    with session_scope(engine) as session:
        sources = ConferenceSourceRepository(session)
        source = sources.list_sources()[0]

        assert source.status == "crawled"
        assert [speaker.full_name for speaker in source.speakers] == ["Jane Roe", "Alex Poe"]


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


class MissingSponsorsFetcher:
    def fetch(self, url: str):
        pages = {
            "https://example.com/conf": (
                200,
                """
                <html><body>
                  <a href="/program">Программа</a>
                  <a href="/partners">Партнеры</a>
                  <section>
                    <h2>Программа</h2>
                    <article class="speaker-card">
                      <h3>Jane Roe</h3>
                      <p>VP Marketing, Bright AI</p>
                    </article>
                  </section>
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
                </body></html>
                """,
            ),
            "https://example.com/partners": (
                200,
                """
                <html><body>
                  <section>
                    <h2>Партнеры</h2>
                    <div class="partner-card"><img alt="North Star AI" src="/logo.png" /></div>
                  </section>
                </body></html>
                """,
            ),
        }
        return pages[url]


class EventIndexFetcher:
    def fetch(self, url: str):
        pages = {
            "https://example.com/home": (
                200,
                """
                <html><body>
                  <a href="/events">Мероприятия</a>
                  <a href="/partnership">Партнерство</a>
                </body></html>
                """,
            ),
            "https://example.com/events": (
                200,
                """
                <html><body>
                  <a href="/event/conferences/future-ai-2026">Future AI 2026</a>
                </body></html>
                """,
            ),
            "https://example.com/event/conferences/future-ai-2026": (
                200,
                """
                <html><body>
                  <section>
                    <h2>Speakers</h2>
                    <article class="speaker-card">
                      <h3>Jane Roe</h3>
                      <p>VP Marketing, Bright AI</p>
                    </article>
                  </section>
                  <section>
                    <h2>Partners</h2>
                    <div class="partner-card"><img alt="North Star AI" src="/logo.png" /></div>
                  </section>
                </body></html>
                """,
            ),
        }
        return pages[url]


class PartnerPriorityFetcher:
    def fetch(self, url: str):
        if url == "https://example.com/conf":
            speaker_links = "\n".join(
                f'<a href="/speakers/profile-{index}">Спикер {index}</a>'
                for index in range(1, 15)
            )
            return 200, f"""
                <html><body>
                  {speaker_links}
                  <a href="/partners">Партнеры</a>
                </body></html>
            """
        if url == "https://example.com/partners":
            return 200, """
                <html><body>
                  <section>
                    <h2>Партнеры</h2>
                    <div class="partner-card"><img alt="North Star AI" src="/logo.png" /></div>
                  </section>
                </body></html>
            """
        if "/speakers/profile-" in url:
            index = url.rsplit("-", 1)[-1]
            return 200, f"""
                <html><body>
                  <article class="speaker-card">
                    <h3>Jane Roe {index}</h3>
                    <p>VP Marketing, Bright AI</p>
                  </article>
                </body></html>
            """
        raise KeyError(url)


class MissingSideAi:
    def extract_from_pages(self, conference_url: str, pages: list[dict[str, str]]):
        return ConferenceExtractionResult(
            speakers=[
                SpeakerResult(
                    full_name="Jane Roe",
                    first_name="Jane",
                    last_name="Roe",
                    title="VP Marketing",
                    company="Bright AI",
                    regalia_raw="VP Marketing, Bright AI",
                )
            ],
            sponsors=[
                SponsorResult(name="North Star AI", category="partner"),
            ],
        )

    def refine(self, conference_url: str, html: str, extracted):
        return extracted


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

        assert source.status == "failed"
        assert source.speakers == []
        assert source.sponsors == []
        assert job.status == "failed"


def test_process_next_job_discovers_partner_pages_for_missing_sponsors(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)

    with session_scope(engine) as session:
        sources = ConferenceSourceRepository(session)
        jobs = JobRepository(session)
        sources.import_seed_urls(["https://example.com/conf"])
        source = sources.list_sources()[0]
        jobs.enqueue_crawl(source.id)

    processed = process_next_job(engine, fetcher=MissingSponsorsFetcher())

    assert processed is True

    with session_scope(engine) as session:
        sources = ConferenceSourceRepository(session)
        source = sources.list_sources()[0]

        assert source.status == "crawled"
        assert [speaker.full_name for speaker in source.speakers] == ["Jane Roe"]
        assert [sponsor.name for sponsor in source.sponsors] == ["North Star AI"]


def test_process_next_job_discovers_event_pages_from_home_index(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)

    with session_scope(engine) as session:
        sources = ConferenceSourceRepository(session)
        jobs = JobRepository(session)
        sources.import_seed_urls(["https://example.com/home"])
        source = sources.list_sources()[0]
        jobs.enqueue_crawl(source.id)

    processed = process_next_job(engine, fetcher=EventIndexFetcher())

    assert processed is True

    with session_scope(engine) as session:
        source = ConferenceSourceRepository(session).list_sources()[0]

        assert source.status == "crawled"
        assert [speaker.full_name for speaker in source.speakers] == ["Jane Roe"]
        assert [sponsor.name for sponsor in source.sponsors] == ["North Star AI"]


def test_process_next_job_prioritizes_partner_pages_over_low_value_speaker_profiles(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)

    with session_scope(engine) as session:
        sources = ConferenceSourceRepository(session)
        jobs = JobRepository(session)
        sources.import_seed_urls(["https://example.com/conf"])
        source = sources.list_sources()[0]
        jobs.enqueue_crawl(source.id)

    processed = process_next_job(engine, fetcher=PartnerPriorityFetcher())

    assert processed is True

    with session_scope(engine) as session:
        source = ConferenceSourceRepository(session).list_sources()[0]

        assert source.status == "crawled"
        assert [sponsor.name for sponsor in source.sponsors] == ["North Star AI"]


def test_process_next_job_uses_ai_pages_extract_to_fill_missing_side(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)

    with session_scope(engine) as session:
        sources = ConferenceSourceRepository(session)
        jobs = JobRepository(session)
        sources.import_seed_urls(["https://example.com/noise"])
        source = sources.list_sources()[0]
        jobs.enqueue_crawl(source.id)

    processed = process_next_job(engine, fetcher=NoiseOnlyFetcher(), ai_refiner=MissingSideAi())

    assert processed is True

    with session_scope(engine) as session:
        sources = ConferenceSourceRepository(session)
        source = sources.list_sources()[0]

        assert source.status == "crawled"
        assert [speaker.full_name for speaker in source.speakers] == ["Jane Roe"]
        assert [sponsor.name for sponsor in source.sponsors] == ["North Star AI"]


class RefiningAi:
    def refine(self, conference_url: str, html: str, extracted):
        extracted.sponsors = []
        return extracted


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


class FewSpeakersFetcher:
    """Fetcher that returns pages with few speakers so AI refine is triggered."""
    def fetch(self, url: str):
        pages = {
            "https://example.com/conf": (
                200,
                """
                <html><body>
                  <a href="/program">Программа</a>
                  <section><h2>Speakers</h2>
                    <article class="speaker-card"><h3>Alice Test</h3><p>CTO, TestCo</p></article>
                  </section>
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


class FewSpeakersRefiner:
    """AI refine adds more speakers when heuristics found < 3."""
    def refine(self, conference_url: str, html: str, extracted):
        extracted.speakers.append(
            SpeakerResult(
                full_name="Jane Roe",
                first_name="Jane",
                last_name="Roe",
                title="CMO",
                company="Bright AI",
                regalia_raw="CMO, Bright AI",
            )
        )
        return extracted


def test_process_next_job_uses_ai_refine_when_few_speakers(tmp_path: Path) -> None:
    """AI refine is triggered when heuristics found < 3 speakers."""
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)

    with session_scope(engine) as session:
        sources = ConferenceSourceRepository(session)
        jobs = JobRepository(session)
        sources.import_seed_urls(["https://example.com/conf"])
        source = sources.list_sources()[0]
        jobs.enqueue_crawl(source.id)

    processed = process_next_job(engine, fetcher=FewSpeakersFetcher(), ai_refiner=FewSpeakersRefiner())

    assert processed is True

    with session_scope(engine) as session:
        source = ConferenceSourceRepository(session).list_sources()[0]
        speaker_names = [speaker.full_name for speaker in source.speakers]
        assert "Alice Test" in speaker_names
        assert "Jane Roe" in speaker_names


class NoisyRefiner:
    """AI refine returns noisy results that should be sanitized."""
    def refine(self, conference_url: str, html: str, extracted):
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


def test_process_next_job_sanitizes_ai_entities_before_saving(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)

    with session_scope(engine) as session:
        sources = ConferenceSourceRepository(session)
        jobs = JobRepository(session)
        sources.import_seed_urls(["https://example.com/conf"])
        source = sources.list_sources()[0]
        jobs.enqueue_crawl(source.id)

    processed = process_next_job(engine, fetcher=FewSpeakersFetcher(), ai_refiner=NoisyRefiner())

    assert processed is True

    with session_scope(engine) as session:
        source = ConferenceSourceRepository(session).list_sources()[0]
        assert [speaker.full_name for speaker in source.speakers] == ["Jane Roe"]
        assert [sponsor.name for sponsor in source.sponsors] == ["North Star AI"]


class BrokenRefiner:
    """AI refine raises an error, worker should fall back to heuristics."""
    def refine(self, conference_url: str, html: str, extracted):
        raise RuntimeError("gateway timeout")


def test_process_next_job_logs_ai_failure_and_falls_back_to_heuristics(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)

    with session_scope(engine) as session:
        sources = ConferenceSourceRepository(session)
        jobs = JobRepository(session)
        sources.import_seed_urls(["https://example.com/conf"])
        source = sources.list_sources()[0]
        jobs.enqueue_crawl(source.id)

    processed = process_next_job(engine, fetcher=MultiPageFetcher(), ai_refiner=BrokenRefiner())

    assert processed is True

    with session_scope(engine) as session:
        source = ConferenceSourceRepository(session).list_sources()[0]
        events = ActivityEventRepository(session).list_recent(limit=10)

        assert [speaker.full_name for speaker in source.speakers] == ["Jane Roe"]
        assert any("AI очистка недоступна" in event.title for event in events)


class TlsBlockedFetcher:
    def fetch(self, url: str):
        raise RuntimeError("_ssl.c:993: The handshake operation timed out")


def test_process_next_job_marks_external_blockers_as_blocked(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    create_schema(engine)

    with session_scope(engine) as session:
        sources = ConferenceSourceRepository(session)
        jobs = JobRepository(session)
        sources.import_seed_urls(["https://example.com/conf"])
        source = sources.list_sources()[0]
        jobs.enqueue_crawl(source.id)

    processed = process_next_job(engine, fetcher=TlsBlockedFetcher())

    assert processed is True

    with session_scope(engine) as session:
        source = ConferenceSourceRepository(session).list_sources()[0]
        job = JobRepository(session).list_jobs()[0]

        assert source.status == "blocked"
        assert "External blocker" in (source.notes or "")
        assert job.status == "failed"
