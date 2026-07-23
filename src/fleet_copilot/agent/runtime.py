"""Turn orchestration — the seam between the API and the graph.

Responsibilities kept here rather than in the graph:

* the tenant binding is read from the *thread record*, never from the request
  body, so a caller cannot switch a conversation to another company mid-stream;
* an MCP tool server is started and torn down around each invocation;
* an ``interrupt`` is translated into a response the UI can render, and a
  resume is translated back into ``Command(resume=...)``.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

from langgraph.types import Command

from ..config import settings
from ..domain.charts import ChartData
from ..domain.enums import ActionStatus
from ..domain.errors import TenantViolation
from ..domain.models import (
    ActionDecision,
    Claim,
    CopilotResponse,
    Evidence,
    Finding,
    ProposedAction,
    Refusal,
)
from ..evidence.review import assess_answer, assess_proposal
from ..storage.db import connect
from ..storage.repositories.actions import ActionRepository
from ..storage.repositories.audit import ThreadRepository
from ..storage.repositories.turns import TurnRepository
from .checkpointer import open_checkpointer
from .graph import build_graph
from .mcp_client import open_tool_session
from .state import new_state


@dataclass
class TurnRequest:
    thread_id: str
    company_id: str
    question: str
    source: str = "chat"


class ApprovalExpired(RuntimeError):
    """A decision arrived for a turn that is no longer waiting on one.

    Treated as a hard failure rather than something to recover from: replaying
    the turn to "catch up" would propose a fresh action and immediately consume
    the decision on it, which is an approval nobody actually gave.
    """


class TurnTimeout(RuntimeError):
    """A turn ran past its overall budget and was cancelled.

    The per-call OpenAI timeout does not bound a turn: planning, dispatch, two
    worker loops and grounding can be a dozen calls, each individually within
    its limit. Cancellation happens before the approval gate resolves, so
    nothing can have been executed.
    """


def _unwrap(exc: BaseException) -> BaseException:
    """Pull the real exception out of an anyio TaskGroup's ExceptionGroup.

    The MCP tool session runs over stdio via anyio TaskGroups (one for the
    subprocess transport, one for the client session). Any exception raised
    inside a turn — a rate-limited OpenAI call, a tenant violation, anything —
    surfaces here wrapped in one or more nested ``ExceptionGroup``s rather than
    raised directly, so a router's ``except TenantViolation`` or
    ``except openai.APIError`` never matches without this. Duck-typed on
    ``.exceptions`` rather than ``isinstance(..., BaseExceptionGroup)`` so this
    also works on Python 3.10, where that class doesn't exist as a builtin.
    """
    seen = exc
    while getattr(seen, "exceptions", None):
        seen = seen.exceptions[0]  # type: ignore[attr-defined]
    return seen


def create_thread(company_id: str, title: str | None = None) -> str:
    thread_id = f"thr-{uuid.uuid4().hex[:12]}"
    with connect() as conn:
        ThreadRepository(conn).create(thread_id, company_id, title)
    return thread_id


def resolve_thread_company(thread_id: str, claimed_company_id: str) -> str:
    """Return a thread's bound tenant, rejecting any attempt to change it.

    The dropdown selects the tenant when a conversation starts. Later turns must
    match — a request that claims a different company for an existing thread is a
    cross-tenant attempt, not a preference.
    """
    with connect() as conn:
        bound = ThreadRepository(conn).company_for(thread_id)
    if bound is None:
        raise TenantViolation("Unknown conversation thread.", thread_id=thread_id)
    if claimed_company_id and claimed_company_id != bound:
        raise TenantViolation(
            "This conversation belongs to a different company. "
            "Start a new conversation to ask about another tenant.",
            thread_id=thread_id,
        )
    return bound


async def run_turn(request: TurnRequest) -> CopilotResponse:
    """Run one question to completion, or to the approval gate."""
    company_id = resolve_thread_company(request.thread_id, request.company_id)
    turn_id = f"turn-{uuid.uuid4().hex[:10]}"
    state = new_state(
        thread_id=request.thread_id,
        turn_id=turn_id,
        company_id=company_id,
        question=request.question,
        source=request.source,
    )

    async with open_checkpointer() as checkpointer:
        graph = build_graph(checkpointer)
        try:
            async with open_tool_session(company_id, request.thread_id) as session:
                config = {
                    "configurable": {
                        "thread_id": request.thread_id,
                        "tool_session": session,
                    },
                    "recursion_limit": 50,
                }
                # A thread paused at the approval gate cannot start a new turn —
                # LangGraph would leave ainvoke waiting on the old interrupt.
                snapshot = await graph.aget_state(config)
                if getattr(snapshot, "next", None):
                    raise ApprovalExpired(
                        "This conversation is waiting on an approval decision. "
                        "Approve or reject the pending actions in Approvals, or "
                        "start a new conversation to ask something else."
                    )
                result = await asyncio.wait_for(
                    graph.ainvoke(state, config=config),
                    timeout=settings.turn_timeout_seconds,
                )
        except asyncio.TimeoutError as exc:
            raise TurnTimeout(
                f"The turn exceeded {settings.turn_timeout_seconds:.0f}s and was "
                "stopped. Nothing was executed."
            ) from exc
        except Exception as exc:
            raise _unwrap(exc) from exc
        response = _to_response(request.thread_id, company_id, result)
        _persist_turn(turn_id, request.question, request.source, response)
        return response


async def resume_turn(
    thread_id: str, company_id: str, decisions: list[ActionDecision]
) -> CopilotResponse:
    """Deliver approval decisions and let the suspended turn finish."""
    bound = resolve_thread_company(thread_id, company_id)

    async with open_checkpointer() as checkpointer:
        graph = build_graph(checkpointer)
        try:
            async with open_tool_session(bound, thread_id) as session:
                config = {
                    "configurable": {"thread_id": thread_id, "tool_session": session},
                    "recursion_limit": 50,
                }

                # There must actually be a turn paused at the approval gate.
                #
                # Without this check a lost checkpoint silently becomes an
                # auto-approval: LangGraph treats Command(resume=...) against an
                # unknown thread as a fresh run, so the graph replays from the
                # start, the worker proposes an action again, and the decision
                # meant for the *previous* proposal is consumed by the interrupt
                # milliseconds later — approving something no human ever saw.
                # Observed in practice when the API reloaded and cleared the
                # in-memory checkpointer between proposal and approval.
                snapshot = await graph.aget_state(config)
                if not getattr(snapshot, "next", None):
                    raise ApprovalExpired(
                        "That approval request is no longer active — the "
                        "conversation it belonged to is not waiting on a "
                        "decision. Ask again to get a fresh proposal. Nothing "
                        "was carried out."
                    )

                result = await asyncio.wait_for(
                    graph.ainvoke(
                        Command(resume=[d.model_dump() for d in decisions]),
                        config=config,
                    ),
                    timeout=settings.turn_timeout_seconds,
                )
        except asyncio.TimeoutError as exc:
            raise TurnTimeout(
                f"The turn exceeded {settings.turn_timeout_seconds:.0f}s and was "
                "stopped."
            ) from exc
        except Exception as exc:
            raise _unwrap(exc) from exc
        response = _to_response(thread_id, bound, result)
        # Update the stored turn — same turn_id, restored from the checkpoint —
        # so its persisted result reflects what the approval produced.
        _persist_turn(
            result.get("turn_id") or "",
            result.get("question") or "",
            result.get("source") or "chat",
            response,
        )
        return response


def _to_response(
    thread_id: str, company_id: str, result: dict[str, Any]
) -> CopilotResponse:
    interrupts = result.get("__interrupt__") or []
    awaiting = bool(interrupts)

    pending: list[ProposedAction] = []
    seen: set[str] = set()

    # Already-decided rows first so a partial approval can stamp Approved on
    # the chat gate while the interrupt still lists what is left open.
    for item in result.get("executed") or []:
        action = ProposedAction.model_validate(item)
        pending.append(action)
        seen.add(action.action_id)

    if awaiting:
        payload = interrupts[0].value if hasattr(interrupts[0], "value") else {}
        for item in (payload or {}).get("actions", []):
            action = ProposedAction.model_validate(item)
            if action.action_id in seen:
                continue
            pending.append(action)
            seen.add(action.action_id)

    refusal = None
    if result.get("refusal_reason"):
        refusal = Refusal(
            reason=result["refusal_reason"], message=result.get("refusal_message", "")
        )

    evidence_by_id = result.get("evidence") or {}
    # The signal is normally computed at proposal time, where the evidence
    # ledger is still resolvable, and stored with the action. This only fills in
    # an older proposal that predates that.
    for action in pending:
        if action.review is None:
            action.review = assess_proposal(action, evidence_by_id)

    claims = [Claim.model_validate(c) for c in (result.get("claims") or [])]
    cited = {eid for c in claims for eid in c.evidence_ids}
    evidence = [
        Evidence.model_validate(record)
        for eid, record in (result.get("evidence") or {}).items()
        if eid in cited
    ]

    answer = result.get("answer") or ""
    if awaiting and not answer:
        answer = "I have prepared the following actions for your approval."

    return CopilotResponse(
        thread_id=thread_id,
        company_id=company_id,
        answer=answer,
        claims=claims,
        evidence=evidence,
        findings=[Finding.model_validate(f) for f in (result.get("findings") or [])],
        charts=[ChartData.model_validate(c) for c in (result.get("charts") or [])],
        pending_actions=pending,
        quality=assess_answer(
            claims_kept=len(claims),
            rejected_claims=result.get("rejected_claims") or [],
            grounding_retries=int(result.get("grounding_retries") or 0),
            tool_errors=result.get("tool_errors") or [],
            evidence_records=len(evidence_by_id),
            charts_rejected=len(result.get("rejected_charts") or []),
        ),
        refusal=refusal,
        awaiting_approval=awaiting,
    )


def _persist_turn(
    turn_id: str, question: str, source: str, response: CopilotResponse
) -> None:
    """Store a completed turn's result. Never allowed to fail the turn itself."""
    if not turn_id:
        return
    try:
        with connect() as conn:
            TurnRepository(conn).upsert(
                turn_id=turn_id,
                thread_id=response.thread_id,
                company_id=response.company_id,
                kind=source,
                question=question,
                result=response.model_dump(mode="json"),
            )
    except Exception:  # pragma: no cover - persistence must not break a turn
        pass


def thread_pending_actions(thread_id: str, company_id: str) -> list[ProposedAction]:
    with connect() as conn:
        actions = ActionRepository(conn).list_for_thread(thread_id)
    return [
        a
        for a in actions
        if a.company_id == company_id and a.status is ActionStatus.PROPOSED
    ]
