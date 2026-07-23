"""MCP server for the email tool.

A second, single-purpose MCP server — separate from the fleet tool server —
that exposes one tool, ``send_email``, over stdio:

    python -m fleet_copilot.email_server.server

It wraps the same :mod:`fleet_copilot.services.email` service the rest of the
app uses, so behaviour is identical: a real send only when SMTP is configured,
otherwise a recorded simulation. Any MCP client can connect to it and send mail.

A note on how it relates to the agent. The agent must not call ``send_email``
directly — that would put a real side effect *before* the human approval gate,
which the whole action model exists to prevent. So the agent's route to email is
its ``notify_employee`` **proposal**: once a human approves it, the action
service sends the message through this same service. This server is the tool made
available on its own for direct or external use; the approval-gated path is how a
notification reaches a mailbox from a conversation.
"""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..services.email import email_enabled, send_email


def send_email_tool(
    to: str,
    subject: str,
    body: str,
) -> dict[str, Any]:
    """Send an email to one recipient.

    Sends for real only when SMTP is configured on the server; otherwise the
    message is simulated (accepted and reported, not transmitted). Returns the
    outcome — ``sent``, ``simulated`` or ``failed`` — never raising, so a caller
    can record what happened.
    """
    result = send_email(to=to, subject=subject, text_content=body)
    return {
        "to": to,
        "subject": subject,
        "status": result.status,
        "delivered": result.status == "sent",
        "error": result.error,
    }


def build_server() -> FastMCP:
    server = FastMCP(
        name="fleet-copilot-email",
        instructions=(
            "Sends email. One tool, send_email(to, subject, body). Delivery is "
            "real only when the server has SMTP configured; otherwise the send is "
            "simulated and reported as such. This tool performs a side effect — a "
            "conversation should reach it only after human approval, via the "
            "notify_employee action, not by calling it mid-turn."
        ),
    )
    server.add_tool(send_email_tool, name="send_email")
    return server


def main() -> None:  # pragma: no cover - process entry point
    mode = "live SMTP" if email_enabled() else "simulated (no SMTP configured)"
    # To stderr, so it never corrupts the stdio protocol on stdout.
    import sys

    print(f"[email-server] starting — delivery mode: {mode}", file=sys.stderr)
    build_server().run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
