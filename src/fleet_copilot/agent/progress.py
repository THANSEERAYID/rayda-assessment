"""Live progress from a running turn.

A turn takes many seconds — planning, dispatch, a bounded tool loop per agent,
grounding, sometimes a corrective retry. A spinner for all of that tells the
administrator nothing about whether the agent is working or stuck, and hides the
part that is actually interesting: which agent is running and what it just
looked at.

Nodes already record every step for the trace. This publishes those same steps
to whoever is listening *while* the turn runs, so the chat can narrate itself.

The listener is reached through a context variable rather than a parameter
threaded through every node, because the graph invokes nodes in asyncio tasks it
creates itself — a contextvar is copied into those tasks automatically, whereas
an argument would have to be plumbed through LangGraph's internals. When nobody
is listening the contextvar is unset and publishing is a no-op, so the plain
request/response path is unaffected.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

# Set for the duration of a streamed turn; unset otherwise.
_current: ContextVar["ProgressStream | None"] = ContextVar(
    "fleet_copilot_progress", default=None
)


@dataclass
class ProgressStream:
    """A queue of events for one in-flight turn."""

    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    _closed: bool = False

    def publish(self, event: dict[str, Any]) -> None:
        """Queue an event. Never blocks and never raises into a node."""
        if self._closed:
            return
        try:
            self.queue.put_nowait(event)
        except Exception:  # pragma: no cover - a full queue must not fail a turn
            pass

    def close(self) -> None:
        self._closed = True
        try:
            self.queue.put_nowait(None)  # sentinel: no more events
        except Exception:  # pragma: no cover
            pass


def bind(stream: ProgressStream) -> None:
    """Attach a stream to the current context, and to tasks spawned from it."""
    _current.set(stream)


def unbind() -> None:
    _current.set(None)


def publish(event: dict[str, Any]) -> None:
    """Emit an event if a listener is attached, otherwise do nothing."""
    stream = _current.get()
    if stream is not None:
        stream.publish(event)


# ---------------------------------------------------------------------------
# Turning a recorded step into something worth reading
# ---------------------------------------------------------------------------
_AGENT_NAMES = {
    "qa_agent": "Q&A agent",
    "insight_agent": "Insight agent",
    "action_agent": "Action agent",
}


def describe_step(node: str, status: str, detail: dict[str, Any]) -> tuple[str, str]:
    """Render a step as (phase, sentence) for a person watching it happen.

    Deliberately written as narration rather than node names: "Checking
    compliance status" is what the reader wants, not "qa_agent/get_compliance_status".
    """
    detail = detail or {}

    if node == "plan":
        intent = str(detail.get("intent") or "")
        if intent == "out_of_scope":
            return "plan", "This is outside what the telemetry can answer"
        return "plan", "Working out what the question needs"

    if node == "manager":
        agents = detail.get("dispatched") or []
        names = " then ".join(_AGENT_NAMES.get(a, a) for a in agents)
        return "manager", f"Handing off to the {names}" if names else "Choosing an agent"

    if node.startswith("worker:"):
        agent = _AGENT_NAMES.get(node.split(":", 1)[1], node.split(":", 1)[1])
        if status == "done":
            return "worker", f"{agent} has what it needs"
        if status in {"circuit_broken", "no_progress", "budget_exceeded", "truncated"}:
            return "worker", f"{agent} stopped early — {detail.get('reason', status)}"
        return "worker", f"{agent} is deciding what to look at"

    if "/" in node:
        tool = node.split("/", 1)[1]
        return "tool", _describe_tool(tool, detail, status)

    if node.startswith("ground"):
        if status == "rejected":
            kept = detail.get("valid_claims")
            total = detail.get("claims")
            return (
                "ground",
                f"Dropped unsupported statements — keeping {kept} of {total}, rewriting",
            )
        if status == "no_evidence":
            return "ground", "Nothing was retrieved, so there is nothing to state"
        return "ground", "Checking every statement against the evidence"

    if node == "human_approval":
        count = len(detail.get("action_ids") or [])
        if detail.get("follow_up"):
            return (
                "approval",
                f"Still waiting on {count} action{'' if count == 1 else 's'}",
            )
        return "approval", f"Prepared {count} action{'' if count == 1 else 's'} for your approval"

    if node == "execute_action":
        done = len(detail.get("executed") or [])
        rejected = len(detail.get("rejected") or [])
        still = detail.get("still_awaiting")
        base = f"Carrying out {done} approved action{'' if done == 1 else 's'}"
        if rejected:
            base += f", rejecting {rejected}"
        if still:
            base += f" — {still} still open"
        return "execute", base

    if node == "respond":
        return "respond", "Writing the answer"

    if node == "refuse":
        return "refuse", str(detail.get("message") or "Cannot answer that")

    return "step", node


def _describe_tool(tool: str, detail: dict[str, Any], status: str) -> str:
    args = detail.get("args") or {}
    summary = detail.get("summary") or {}

    if status == "error":
        return f"{tool} failed — {summary.get('message') or summary.get('error') or 'error'}"

    if tool == "run_insight_scan":
        detectors = args.get("detectors")
        what = ", ".join(str(d).replace("_", " ") for d in detectors) if detectors else "all detectors"
        count = summary.get("finding_count")
        found = f" — {count} finding{'' if count == 1 else 's'}" if count is not None else ""
        return f"Scanning for {what}{found}"

    if tool == "query_devices":
        count = summary.get("match_count")
        found = f" — {count} match{'es' if count != 1 else ''}" if count is not None else ""
        return f"Searching the fleet{found}"

    if tool == "get_compliance_status":
        count = summary.get("match_count")
        found = f" — {count} result{'' if count == 1 else 's'}" if count is not None else ""
        return f"Checking compliance{found}"

    if tool == "get_device_history":
        device = args.get("device_id", "a device")
        metric = str(args.get("metric", "")).replace("_", " ")
        return f"Reading {metric} history for {device}"

    if tool == "get_device_snapshot":
        return f"Opening the raw record for {args.get('device_id', 'a device')}"

    if tool == "list_fleet_summary":
        count = summary.get("device_count")
        return f"Getting an overview{f' — {count} devices' if count is not None else ''}"

    if tool in {
        "create_upgrade_order",
        "open_remediation_ticket",
        "flag_device_for_replacement",
        "notify_employee",
    }:
        readable = tool.replace("_", " ")
        target = args.get("device_id") or args.get("employee_id") or ""
        if summary.get("error"):
            return f"Could not propose {readable} — {summary.get('message', 'refused')}"
        return f"Proposing {readable}{f' for {target}' if target else ''}"

    return tool.replace("_", " ")
