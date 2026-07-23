"""Engine and connection management."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import Connection
from sqlalchemy.pool import StaticPool

from ..config import settings
from .tables import metadata

_engine: Engine | None = None


def create_db_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    """Build an engine.

    In-memory SQLite needs ``StaticPool`` so every connection sees the same
    database — without it each checkout gets a fresh empty one and the seeded
    evaluation fixtures vanish.
    """
    url = url or settings.database_url
    kwargs: dict[str, object] = {"echo": echo, "future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _enable_fk(dbapi_conn, _record):  # pragma: no cover - trivial
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    return engine


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_db_engine()
    return _engine


def set_engine(engine: Engine) -> None:
    """Override the process-wide engine (used by the evaluation fixtures)."""
    global _engine
    _engine = engine


def create_schema(engine: Engine | None = None) -> None:
    metadata.create_all(engine or get_engine())


def drop_schema(engine: Engine | None = None) -> None:
    metadata.drop_all(engine or get_engine())


@contextmanager
def connect() -> Iterator[Connection]:
    """A transactional connection. Commits on success, rolls back on error."""
    with get_engine().begin() as conn:
        yield conn
