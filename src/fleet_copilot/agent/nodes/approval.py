"""Human-in-the-loop approval gate.

``interrupt()`` suspends the graph here and the checkpointer persists the whole
turn. Execution resumes only when the API delivers explicit decisions through
``Command(resume=...)``, which means "execute without approval" is not a rule the
model is asked to follow — there is simply no edge from proposal to execution
that does not pass through this pause.

One interrupt covers the open proposals from a turn. The reviewer may decide a
subset; those run immediately, then the graph returns here and interrupts again
for whatever is still undecided. That keeps partial approvals in the same
trace (approval → execute → approval → …) instead of orphaning the rest.
"""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from ...domain.enums import ActionStatus, AuditEventType
from ...domain.models import ActionDecision
from ...services.actions import ActionService
from ...storage.db import connect
from ..state import AgentState
from ._common import (
    record_audit,
    record_step,
    step_already_recorded,
    validated_node,
)


def _decided_ids(state: AgentState) -> set[str]:
    return {
        str(item.get("action_id"))
        for item in state.executed
        if item.get("action_id")
    }


def _remaining_proposals(state: AgentState) -> list[dict]:
    done = _decided_ids(state)
    return [p for p in state.proposals if p.get("action_id") not in done]


@validated_node
async def approval_node(state: AgentState, config: RunnableConfig) -> dict:
    remaining = _remaining_proposals(state)
    follow_up = bool(state.executed)
    action_ids = [p["action_id"] for p in remaining]

    # Everything from here to interrupt() runs a second time when the graph
    # resumes: LangGraph replays the node from the top, and interrupt() returns
    # the decisions instead of suspending. Without this check one pause writes
    # two trace rows and two audit entries — a double count on the one step a
    # human is accountable for.
    replaying = step_already_recorded(state, "human_approval")

    seq = record_step(
        state,
        "human_approval",
        "waiting",
        {
            "action_ids": action_ids,
            "count": len(remaining),
            "follow_up": follow_up,
        },
        once=True,
    )
    if not replaying:
        if not follow_up:
            record_audit(
                state,
                AuditEventType.ACTION_PROPOSED,
                f"Awaiting human approval for {len(remaining)} action(s)",
                {"action_ids": action_ids},
            )
        else:
            record_audit(
                state,
                AuditEventType.ACTION_PROPOSED,
                f"Still awaiting approval for {len(remaining)} action(s)",
                {"action_ids": action_ids, "follow_up": True},
            )

    # Suspends here. The value below is what the API surfaces to the reviewer.
    decisions = interrupt(
        {
            "type": "approval_required",
            "company_id": state.company_id,
            "thread_id": state.thread_id,
            "answer": state.answer,
            "actions": remaining,
        }
    )

    return {
        "decisions": _normalise(decisions),
        "step_seq": seq,
        "awaiting_approval": True,
    }


def _normalise(payload) -> list[dict]:
    """Accept the several shapes a resume value can arrive in."""
    if payload is None:
        return []
    if isinstance(payload, dict):
        payload = payload.get("decisions", [])
    normalised: list[dict] = []
    for item in payload or []:
        if isinstance(item, ActionDecision):
            normalised.append(item.model_dump())
        elif isinstance(item, dict):
            normalised.append(item)
    return normalised


@validated_node
async def execute_action_node(state: AgentState, config: RunnableConfig) -> dict:
    """Apply the human's decisions for this resume batch.

    Reachable only after :func:`approval_node` has resumed. Approval and
    execution are delegated to :class:`ActionService`, whose repository refuses
    any transition that did not come from ``APPROVED``.

    Results accumulate on ``state.executed`` so a later approval round can see
    which proposals are already settled.
    """
    raw = state.decisions
    decisions = [ActionDecision.model_validate(d) for d in raw]

    with connect() as conn:
        service = ActionService(
            conn, state.company_id, state.thread_id or "unknown"
        )
        results = service.apply_decisions(decisions, decided_by="it-admin")

    batch = [r.model_dump(mode="json") for r in results]
    accumulated = list(state.executed) + batch
    approved = [r for r in results if r.status is ActionStatus.EXECUTED]
    rejected = [r for r in results if r.status is ActionStatus.REJECTED]
    still_open = [
        p
        for p in state.proposals
        if p.get("action_id") not in {str(a.get("action_id")) for a in accumulated}
    ]

    seq = record_step(
        state,
        "execute_action",
        "ok",
        {
            "executed": [r.action_id for r in approved],
            "rejected": [r.action_id for r in rejected],
            "still_awaiting": len(still_open),
        },
    )
    return {
        "executed": accumulated,
        "awaiting_approval": bool(still_open),
        "step_seq": seq,
    }


def route_after_execute(state: AgentState) -> str:
    """Pause again when any proposal from this turn is still undecided."""
    return "approval" if _remaining_proposals(state) else "respond"
