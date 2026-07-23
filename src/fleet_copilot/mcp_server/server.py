"""The MCP tool server.

Started once per conversation thread with the tenant already fixed:

    python -m fleet_copilot.mcp_server.server --company-id acme-001 --thread-id t-123

Binding the tenant at launch rather than passing it per call is the strongest
form of the isolation guarantee available here. A server instance serving an
Acme session has no argument, and no code path, that reaches another company's
telemetry — the scope is a property of the process, not of a parameter the model
could be talked into changing.

Transport is stdio, so the agent supervises the server as a child process and it
exits with the session.
"""
from __future__ import annotations

import argparse
import os

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.prompts import Prompt

from ..config import settings
from .context import ServerContext, set_context
from .prompts import PROMPTS
from .tools import actions as action_tools
from .tools import read as read_tools

READ_TOOLS = (
    read_tools.list_fleet_summary,
    read_tools.query_devices,
    read_tools.get_compliance_status,
    read_tools.get_device_history,
    read_tools.get_device_snapshot,
    read_tools.run_insight_scan,
    read_tools.run_read_query,
)

ACTION_TOOLS = (
    action_tools.create_upgrade_order,
    action_tools.open_remediation_ticket,
    action_tools.flag_device_for_replacement,
    action_tools.notify_employee,
    action_tools.list_pending_actions,
)


def build_server(company_id: str, *, thread_id: str | None = None, database_url: str | None = None) -> FastMCP:
    """Construct a tenant-bound server instance."""
    ctx = ServerContext.create(company_id, database_url=database_url)
    ctx.thread_id = thread_id
    set_context(ctx)

    server = FastMCP(
        name=f"fleet-copilot-tools[{company_id}]",
        instructions=(
            "Telemetry tools for a single company's device fleet. The tenant is "
            "fixed by the session: never pass a company_id argument. Read tools "
            "return an 'evidence' array whose ids must be cited in any claim you "
            "make. Action tools only create proposals for human approval."
        ),
    )
    for tool in (*READ_TOOLS, *ACTION_TOOLS):
        server.add_tool(tool)
    # Prompts are the user-controlled primitive — an administrator picks one, the
    # model does not invoke them. Registered here so any MCP client discovers the
    # same workflows rather than each one hardcoding its own.
    for prompt in PROMPTS:
        server.add_prompt(Prompt.from_function(prompt))
    return server


def main() -> None:  # pragma: no cover - process entry point
    parser = argparse.ArgumentParser(description="Fleet Copilot MCP tool server")
    parser.add_argument(
        "--company-id",
        default=os.environ.get("FLEET_COMPANY_ID"),
        help="Tenant this server instance is bound to (required).",
    )
    parser.add_argument(
        "--thread-id",
        default=os.environ.get("FLEET_THREAD_ID"),
        help="Conversation thread, recorded on audit entries.",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", settings.database_url),
    )
    args = parser.parse_args()
    if not args.company_id:
        parser.error("--company-id (or FLEET_COMPANY_ID) is required")

    server = build_server(
        args.company_id,
        thread_id=args.thread_id,
        database_url=args.database_url,
    )
    server.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
