from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from conference_leads_collector.storage.models import (
    ActivityEvent,
    ConferenceSource,
    CrawlJob,
    DiscoveredPage,
    Speaker,
    Sponsor,
    TenchatProfile,
)


def _normalize_seed_url(raw_url: str) -> str:
    trimmed = raw_url.strip()
    if "://" not in trimmed:
        trimmed = f"https://{trimmed}"

    parsed = urlsplit(trimmed)
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, "", ""))


class ConferenceSourceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def import_seed_urls(self, urls: list[str]) -> dict[str, int]:
        normalized_urls = [_normalize_seed_url(url) for url in urls]
        existing_urls = set(
            self.session.scalars(
                select(ConferenceSource.seed_url).where(ConferenceSource.seed_url.in_(normalized_urls))
            )
        )

        inserted = 0
        skipped = 0
        seen_in_batch: set[str] = set()

        for url in normalized_urls:
            if url in existing_urls or url in seen_in_batch:
                skipped += 1
                continue

            self.session.add(ConferenceSource(seed_url=url, status="pending"))
            seen_in_batch.add(url)
            inserted += 1

        self.session.flush()
        return {"inserted": inserted, "skipped": skipped}

    def list_sources_by_urls(self, urls: list[str]) -> list[ConferenceSource]:
        normalized_urls = [_normalize_seed_url(url) for url in urls]
        return list(
            self.session.scalars(
                select(ConferenceSource).where(ConferenceSource.seed_url.in_(normalized_urls)).order_by(ConferenceSource.id.asc())
            )
        )

    def list_sources(self) -> list[ConferenceSource]:
        return list(
            self.session.scalars(
                select(ConferenceSource)
                .options(
                    selectinload(ConferenceSource.discovered_pages),
                    selectinload(ConferenceSource.speakers),
                    selectinload(ConferenceSource.sponsors),
                )
                .order_by(ConferenceSource.seed_url.asc())
            )
        )

    def get_source(self, source_id: int) -> ConferenceSource | None:
        return self.session.scalar(
            select(ConferenceSource)
            .options(
                selectinload(ConferenceSource.discovered_pages),
                selectinload(ConferenceSource.speakers),
                selectinload(ConferenceSource.sponsors),
            )
            .where(ConferenceSource.id == source_id)
        )

    def mark_pending(self, source_id: int) -> ConferenceSource:
        source = self.get_source(source_id)
        if source is None:
            raise ValueError(f"Conference source {source_id} not found")
        source.status = "pending"
        self.session.flush()
        return source

    def mark_running(self, source_id: int) -> ConferenceSource:
        source = self.get_source(source_id)
        if source is None:
            raise ValueError(f"Conference source {source_id} not found")
        source.status = "running"
        self.session.flush()
        return source

    def mark_failed(self, source_id: int, notes: str | None = None) -> ConferenceSource:
        source = self.get_source(source_id)
        if source is None:
            raise ValueError(f"Conference source {source_id} not found")
        source.status = "failed"
        source.notes = notes
        self.session.flush()
        return source

    def mark_crawled(
        self,
        source_id: int,
        url: str,
        http_status: int,
        html: str,
        speakers: list[dict],
        sponsors: list[dict],
    ) -> None:
        source = self.get_source(source_id)
        if source is None:
            raise ValueError(f"Conference source {source_id} not found")

        source.status = "crawled"
        source.last_crawled_at = datetime.now(UTC)
        source.discovered_pages.clear()
        source.speakers.clear()
        source.sponsors.clear()

        page = DiscoveredPage(
            conference_id=source_id,
            url=url,
            page_type="conference",
            http_status=http_status,
            content_hash=hashlib.sha256(html.encode("utf-8")).hexdigest(),
            crawl_status="done",
            render_mode="http",
            raw_html=html,
        )
        source.discovered_pages.append(page)

        for item in speakers:
            source.speakers.append(
                Speaker(
                    conference_id=source_id,
                    source_page_id=None,
                    full_name=item["full_name"],
                    first_name=item.get("first_name"),
                    last_name=item.get("last_name"),
                    title=item.get("title"),
                    company=item.get("company"),
                    regalia_raw=item.get("regalia_raw"),
                    confidence=item.get("confidence", 80),
                    needs_review=item.get("needs_review", False),
                    raw_fragment=item.get("raw_fragment"),
                )
            )

        for item in sponsors:
            source.sponsors.append(
                Sponsor(
                    conference_id=source_id,
                    source_page_id=None,
                    name=item["name"],
                    category=item.get("category"),
                    description=item.get("description"),
                    website=item.get("website"),
                    confidence=item.get("confidence", 70),
                    needs_review=item.get("needs_review", False),
                    raw_fragment=item.get("raw_fragment"),
                )
            )

        self.session.flush()


class JobRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def enqueue_crawl(self, source_id: int, force: bool = False, priority: int | None = None) -> CrawlJob:
        existing = self.session.scalar(
            select(CrawlJob).where(
                CrawlJob.target_id == source_id,
                CrawlJob.job_type == "crawl_conference",
                CrawlJob.status.in_(("pending", "running")),
            )
        )
        if existing is not None and not force:
            return existing

        job = CrawlJob(target_id=source_id)
        if priority is not None:
            job.priority = priority
        self.session.add(job)
        self.session.flush()
        return job

    def claim_next_job(self) -> CrawlJob | None:
        job = self.session.scalar(
            select(CrawlJob).where(CrawlJob.status == "pending").order_by(CrawlJob.priority.asc(), CrawlJob.id.asc())
        )
        if job is None:
            return None
        job.status = "running"
        job.attempt += 1
        job.started_at = datetime.now(UTC)
        self.session.flush()
        return job

    def mark_done(self, job: CrawlJob) -> None:
        job.status = "done"
        job.finished_at = datetime.now(UTC)
        job.last_error = None
        self.session.flush()

    def mark_failed(self, job: CrawlJob, error: str) -> None:
        job.status = "failed"
        job.finished_at = datetime.now(UTC)
        job.last_error = error
        self.session.flush()

    def list_jobs(self) -> list[CrawlJob]:
        return list(self.session.scalars(select(CrawlJob).order_by(CrawlJob.id.asc())))


class TenchatProfileRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_profile(
        self,
        profile_url: str,
        full_name: str | None,
        job_title: str | None,
        followers: int | None,
        source_query: str,
        confidence: int = 70,
        needs_review: bool = False,
        raw_fragment: str | None = None,
    ) -> TenchatProfile:
        profile = self.session.scalar(select(TenchatProfile).where(TenchatProfile.profile_url == profile_url))
        if profile is None:
            profile = TenchatProfile(profile_url=profile_url)
            self.session.add(profile)

        profile.full_name = full_name
        profile.job_title = job_title
        profile.followers = followers
        profile.source_query = source_query
        profile.confidence = confidence
        profile.needs_review = needs_review
        profile.raw_fragment = raw_fragment
        profile.last_checked_at = datetime.now(UTC)
        self.session.flush()
        return profile

    def list_profiles(self) -> list[TenchatProfile]:
        return list(self.session.scalars(select(TenchatProfile).order_by(TenchatProfile.id.asc())))


class ActivityEventRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_event(self, title: str, details: str | None = None, level: str = "info") -> ActivityEvent:
        event = ActivityEvent(level=level, title=title, details=details)
        self.session.add(event)
        self.session.flush()
        return event

    def list_recent(self, limit: int = 20) -> list[ActivityEvent]:
        return list(
            self.session.scalars(
                select(ActivityEvent).order_by(ActivityEvent.created_at.desc(), ActivityEvent.id.desc()).limit(limit)
            )
        )
