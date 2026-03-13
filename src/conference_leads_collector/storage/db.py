from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine as sa_create_engine
from sqlalchemy.orm import Session, sessionmaker

from conference_leads_collector.storage.models import Base


def create_engine(database_url: str):
    return sa_create_engine(database_url, future=True)


def create_schema(engine) -> None:
    Base.metadata.create_all(engine, checkfirst=True)


@contextmanager
def session_scope(engine) -> Iterator[Session]:
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
