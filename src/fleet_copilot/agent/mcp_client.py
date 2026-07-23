"""MCP session management.

The agent reaches telemetry only through this client. A session is one child
process of the tool server, launched with the tenant already bound, so isolation
is enforced by the process the agent is talking to rather than by anything the
agent remembers to pass.

Sessions are per-invocation rather than long-lived. A turn that pauses at the
approval gate may not resume for hours, and holding a subprocess open across that
gap would be both fragile and pointless — the resume path needs no tools.
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ..config import settings

# Tools that create proposals. Tracked so the graph can tell a retrieval turn
# from one that produced something needing human approval.
ACTION_TOOL_NAMES = {
    "create_upgrade_order",
    "open_remediation_ticket",
    "flag_device_for_replacement",
    "notify_employee",
}


def server_parameters(company_id: str, thread_id: str) -> StdioServerParameters:
    env = dict(os.environ)
    env["DATABASE_URL"] = settings.database_url
    env["FLEET_COMPANY_ID"] = company_id
    env["FLEET_THREAD_ID"] = thread_id
    return StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "fleet_copilot.mcp_server.server",
            "--company-id",
            company_id,
            "--thread-id",
            thread_id,
        ],
        env=env,
    )


class ToolSession:
    """A live MCP session plus its LangChain tool bindings."""

    def __init__(self, session: ClientSession, tools: list[BaseTool]) -> None:
        self.session = session
        self.tools = tools
        self.by_name = {t.name: t for t in tools}

    @property
    def read_tools(self) -> list[BaseTool]:
        return [t for t in self.tools if t.name not in ACTION_TOOL_NAMES]

    def tools_for(self, names: tuple[str, ...] | list[str]) -> list[BaseTool]:
        """The subset a worker is allowed to hold.

        A tool omitted here is not merely discouraged — it never reaches
        ``bind_tools``, so the model has no way to call it. Unknown names are
        skipped rather than raising; the evaluation suite asserts separately
        that every name in the worker registry matches a real tool, which is a
        better place to catch a typo than a failed turn.
        """
        return [self.by_name[name] for name in names if name in self.by_name]

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        tool = self.by_name.get(name)
        if tool is None:
            raise KeyError(f"Unknown tool '{name}'")
        return await tool.ainvoke(args)


@asynccontextmanager
async def open_tool_session(
    company_id: str, thread_id: str
) -> AsyncIterator[ToolSession]:
    """Start a tenant-bound tool server and yield its tools."""
    params = server_parameters(company_id, thread_id)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await load_mcp_tools(session)
            yield ToolSession(session, tools)
