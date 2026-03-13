from __future__ import annotations

from datetime import datetime, UTC

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ConferenceSource(Base):
    __tablename__ = "conference_sources"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    seed_url: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    discovered_pages: Mapped[list["DiscoveredPage"]] = relationship(
        back_populates="conference", cascade="all, delete-orphan"
    )
    speakers: Mapped[list["Speaker"]] = relationship(
        back_populates="conference", cascade="all, delete-orphan"
    )
    sponsors: Mapped[list["Sponsor"]] = relationship(
        back_populates="conference", cascade="all, delete-orphan"
    )


class DiscoveredPage(Base):
    __tablename__ = "discovered_pages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conference_id: Mapped[int] = mapped_column(ForeignKey("conference_sources.id"), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    page_type: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    crawl_status: Mapped[str] = mapped_column(String(32), default="done", nullable=False)
    render_mode: Mapped[str] = mapped_column(String(32), default="http", nullable=False)
    raw_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    conference: Mapped["ConferenceSource"] = relationship(back_populates="discovered_pages")


class Speaker(Base):
    __tablename__ = "speakers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conference_id: Mapped[int] = mapped_column(ForeignKey("conference_sources.id"), nullable=False, index=True)
    source_page_id: Mapped[int | None] = mapped_column(ForeignKey("discovered_pages.id"), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    regalia_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[int] = mapped_column(Integer, default=80, nullable=False)
    needs_review: Mapped[bool] = mapped_column(default=False, nullable=False)
    raw_fragment: Mapped[str | None] = mapped_column(Text, nullable=True)
    conference: Mapped["ConferenceSource"] = relationship(back_populates="speakers")


class Sponsor(Base):
    __tablename__ = "sponsors"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conference_id: Mapped[int] = mapped_column(ForeignKey("conference_sources.id"), nullable=False, index=True)
    source_page_id: Mapped[int | None] = mapped_column(ForeignKey("discovered_pages.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    website: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    confidence: Mapped[int] = mapped_column(Integer, default=70, nullable=False)
    needs_review: Mapped[bool] = mapped_column(default=False, nullable=False)
    raw_fragment: Mapped[str | None] = mapped_column(Text, nullable=True)
    conference: Mapped["ConferenceSource"] = relationship(back_populates="sponsors")


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(String(64), default="crawl_conference", nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), default="conference_source", nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class TenchatProfile(Base):
    __tablename__ = "tenchat_profiles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    profile_url: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    followers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_query: Mapped[str | None] = mapped_column(String(255), nullable=True)
    match_status: Mapped[str] = mapped_column(String(32), default="matched", nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, default=70, nullable=False)
    needs_review: Mapped[bool] = mapped_column(default=False, nullable=False)
    raw_fragment: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class ActivityEvent(Base):
    __tablename__ = "activity_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(32), default="info", nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, index=True
    )
