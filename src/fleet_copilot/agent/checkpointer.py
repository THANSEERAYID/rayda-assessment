"""Checkpointer lifecycle.

The checkpointer is what makes an approval pause resumable: the suspended turn
is persisted at the interrupt, and the later approve/reject call resumes from
exactly that point.

Postgres is the real backing store — a paused approval survives a process
restart, so an administrator can decide on an action long after the conversation
that proposed it.

SQLite falls back to an in-memory saver, and that saver is a **process-lifetime
singleton**. Constructing a fresh one per call would look harmless and quietly
break approval altogether: ``run_turn`` would write its interrupt into one saver
and ``resume_turn`` would look for it in another, so every resume would find
nothing to resume and the action would sit in ``proposed`` forever. The honest
limit of this mode is that it does not survive a restart — not that it does not
work at all.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from functools import lru_cache
from typing import AsyncIterator

from langgraph.checkpoint.memory import InMemorySaver

from ..config import settings


@lru_cache(maxsize=1)
def _memory_saver() -> InMemorySaver:
    """One saver for the process, so a pause and its resume share state."""
    return InMemorySaver()


def reset_memory_saver() -> None:
    """Drop the in-memory saver. For tests that need a clean slate."""
    _memory_saver.cache_clear()


@asynccontextmanager
async def open_checkpointer() -> AsyncIterator[object]:
    if settings.is_sqlite:
        yield _memory_saver()
        return

    import asyncio
    import sys

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    # psycopg's async driver cannot run on a ProactorEventLoop. Uvicorn selects
    # one on Windows unless it is running with a reload supervisor, so this is
    # reachable purely by how the server was launched — check it here and say
    # so, rather than surfacing a psycopg InterfaceError from inside a turn.
    if sys.platform == "win32":
        loop = asyncio.get_running_loop()
        if "Proactor" in type(loop).__name__:
            raise RuntimeError(
                "The Postgres checkpointer needs a SelectorEventLoop, but this "
                f"process is running on {type(loop).__name__}. Start the API "
                "with `python scripts/run_api.py` (or add --reload), which pins "
                "the right loop."
            )

    # psycopg wants a bare DSN; SQLAlchemy's driver prefix is not valid there.
    dsn = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        await saver.setup()
        yield saver
