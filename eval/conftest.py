"""Evaluation fixtures.

The suite has two tiers:

``deterministic/``
    Runs with no model and no API key, against a temporary SQLite database
    seeded from the dataset. This is where correctness of retrieval, detectors,
    tenant isolation and the action state machine is proven, because none of
    those depend on a model and all of them should be exactly reproducible.

``live/``
    Exercises the agent end to end against the real model. Skipped unless
    ``--live`` is passed, so a fresh clone can run ``make eval`` for free.

The MCP tools are tested through a real stdio client session, not by importing
the functions directly — the protocol boundary is part of what is under test, so
bypassing it would leave the actual integration unverified.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from fleet_copilot.config import settings
from fleet_copilot.ingestion.ingest import ingest
from fleet_copilot.storage.db import create_db_engine, set_engine

REPO_ROOT = Path(__file__).resolve().parents[1]


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Run the live agent tier (needs OPENAI_API_KEY; makes real calls).",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="needs --live and OPENAI_API_KEY")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def database_url(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A file-backed SQLite database seeded once for the session.

    File-backed rather than in-memory because the MCP server runs as a separate
    process and cannot see another process's in-memory database.
    """
    path = tmp_path_factory.mktemp("fleet") / "eval.sqlite"
    url = f"sqlite+pysqlite:///{path.as_posix()}"
    engine = create_db_engine(url)
    ingest(engine)
    # Point the whole process at this database for the rest of the session.
    settings.database_url = url
    set_engine(engine)
    return url


@pytest.fixture(scope="session")
def engine(database_url: str):
    return create_db_engine(database_url)


@pytest.fixture()
def conn(engine):
    with engine.begin() as connection:
        yield connection


@pytest.fixture(scope="session")
def mcp_env(database_url: str) -> dict[str, str]:
    env = dict(os.environ)
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    return env


@pytest.fixture()
def mcp_session_factory(mcp_env):
    """Open a tenant-bound MCP session over stdio.

    Usage::

        async with mcp_session_factory("acme-001") as session:
            result = await session.call("query_devices", {...})
    """
    from contextlib import asynccontextmanager

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    @asynccontextmanager
    async def factory(company_id: str, thread_id: str = "t-eval"):
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "fleet_copilot.mcp_server.server",
                "--company-id",
                company_id,
                "--thread-id",
                thread_id,
            ],
            env=mcp_env,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield _Caller(session)

    return factory


class _Caller:
    """Thin wrapper that returns parsed JSON from a tool call."""

    def __init__(self, session) -> None:
        self.session = session

    async def call(self, name: str, args: dict) -> dict:
        import json

        result = await self.session.call_tool(name, args)
        text = result.content[0].text
        return json.loads(text)

    async def tool_names(self) -> list[str]:
        listing = await self.session.list_tools()
        return [t.name for t in listing.tools]
