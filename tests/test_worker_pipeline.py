from pathlib import Path

from conference_leads_collector.services.worker import process_next_job
from conference_leads_collector.storage.db import create_engine, create_schema, session_scope
from conference_leads_collector.storage.repositories import ConferenceSourceRepository, JobRepository


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
