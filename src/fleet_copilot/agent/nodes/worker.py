"""Worker node — one specialized agent's bounded tool-calling loop.

The manager queues an ordered list of workers; this node runs whichever is next
and self-loops until the queue drains. Each invocation binds **only** that
worker's allowed tools, so a capability the worker does not have is absent from
the model's tool list rather than merely discouraged by its prompt.

The loop itself is explicit rather than delegated to a prebuilt agent so the
bounds and the evidence bookkeeping stay visible and testable:

* it stops after the worker's own iteration budget regardless of what the model
  wants;
* every tool result is parsed, its ``evidence`` array folded into the run's
  ledger mirror, and its errors recorded;
* proposals created by action tools are captured so the graph knows a human
  decision is owed;
* a repeated identical call is refused rather than executed — and the record of
  what has been called lives in state, so it spans workers.

Everything a worker retrieves accumulates into shared state, which is what the
next worker receives as its handoff.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ValidationError

from ...domain.enums import AuditEventType
from ...domain.models import Evidence, Finding, ProposedAction
from ...services.history import SeriesPoint
from ...config import settings
from ..llm import LLMBudgetExceeded, get_llm, invoke_llm
from ..mcp_client import ACTION_TOOL_NAMES
from ..state import AgentState
from ..tool_payload import digest, trim_for_model
from ..workers import WorkerName, config_for
from ._common import (
    persist_evidence,
    Timer,
    load_worker_prompt,
    record_audit,
    record_step,
    render_handoff,
    tool_session,
    validated_node,
)


class MalformedToolResult(Exception):
    """A tool returned a payload that does not match its declared shape."""


def _parse(payload: Any) -> dict[str, Any]:
    """Unwrap a tool result into the payload dict the tool actually returned.

    MCP delivers results as *content blocks*, and the LangChain adapter hands
    them over as a list of them — ``[{"type": "text", "text": "{...json...}"}]``.
    Both layers have to come off: returning the block itself yields a useless
    ``{"type", "text"}`` dict, and the real payload never reaches the agent.
    Objects with a ``.text`` attribute are handled too, since the raw MCP client
    returns ``TextContent`` rather than dicts.
    """
    if isinstance(payload, list):
        return _parse(payload[0]) if payload else {}

    # A content block, dict-shaped or object-shaped, wrapping the real payload.
    if isinstance(payload, dict) and "text" in payload and payload.get("type") == "text":
        return _parse(payload["text"])
    text_attr = getattr(payload, "text", None)
    if text_attr is not None and not isinstance(payload, (str, bytes)):
        return _parse(text_attr)

    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
            return parsed if isinstance(parsed, dict) else {"result": parsed}
        except json.JSONDecodeError:
            return {"result": payload}
    return {"result": payload}


def _validated(model: type[BaseModel], records: list[Any], kind: str) -> list[BaseModel]:
    """Parse a tool payload into domain models, failing at the source.

    State stores these as plain dicts so the checkpointer can serialise without a
    custom encoder — but validating here, at the moment a tool hands them over,
    means a malformed record fails at the call that produced it rather than
    several nodes later when the handoff renders ``None`` into a prompt or
    ``_rebuild_ledger`` finally tries to parse it. Same data in state either
    way; the difference is where a bad payload surfaces.
    """
    parsed: list[BaseModel] = []
    for index, record in enumerate(records):
        try:
            parsed.append(model.model_validate(record))
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            location = ".".join(str(p) for p in first.get("loc", ())) or "?"
            raise MalformedToolResult(
                f"{kind}[{index}] is malformed at '{location}': "
                f"{first.get('msg', exc)}"
            ) from exc
    return parsed


@dataclass
class _Ingested:
    """One tool result, parsed into domain models."""

    evidence: list[Evidence] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    proposals: list[ProposedAction] = field(default_factory=list)
    points: list[SeriesPoint] = field(default_factory=list)


def _ingest(result: dict[str, Any], *, tool: str) -> _Ingested:
    """Validate everything a successful tool result contributes to state.

    Raises :class:`MalformedToolResult` on anything that does not parse, so the
    failure is attributed to the call that caused it.

    This covers every field of a tool payload that reaches state. ``points`` is
    included because a trend chart reads that series back by key later, far from
    this call — an unparseable point would otherwise fail inside the chart
    builder with no indication of which tool produced it.
    """
    action = result.get("action")
    return _Ingested(
        evidence=_validated(Evidence, result.get("evidence") or [], "evidence"),
        findings=_validated(Finding, result.get("findings") or [], "findings"),
        proposals=_validated(
            ProposedAction,
            [action] if tool in ACTION_TOOL_NAMES and action else [],
            "action",
        ),
        points=_validated(SeriesPoint, result.get("points") or [], "points"),
    )


@validated_node
async def worker_node(state: AgentState, config: RunnableConfig) -> dict:
    session = tool_session(config)

    queue = state.dispatch_queue
    index = state.dispatch_index
    if index >= len(queue):  # pragma: no cover - routing prevents this
        return {}
    worker = WorkerName(queue[index])
    cfg = config_for(worker)

    # The capability boundary: only this worker's tools are bound.
    model = get_llm().bind_tools(session.tools_for(cfg.allowed_tools))

    task = [
        f"Question: {state.question}",
        "",
        "Retrieval plan:",
        *(f"- {s}" for s in state.plan),
    ]
    handoff = render_handoff(state)
    if handoff:
        task += ["", handoff]

    messages: list = [
        SystemMessage(content=load_worker_prompt(cfg.prompt)),
        HumanMessage(content="\n".join(task)),
    ]

    evidence: dict[str, dict] = dict(state.evidence)
    findings: list[dict] = list(state.findings)
    proposals: list[dict] = list(state.proposals)
    call_log: list[dict] = list(state.tool_calls)
    errors: list[str] = list(state.tool_errors)
    history_series: dict[str, list[dict]] = dict(state.history_series)
    seq = state.step_seq
    seen_calls: set[str] = set(state.seen_tool_calls)
    iterations = 0
    llm_calls = state.llm_calls
    consecutive_errors = 0
    unproductive = 0
    stop_reason = ""

    # Tool payloads stay verbatim for exactly one more round — long enough for
    # the agent to act on what it just fetched — then shrink to a digest. Rounds
    # older than that are re-sent on every remaining pass while contributing
    # nothing the agent has not already used.
    previous_round: list[tuple[int, str, dict]] = []
    this_round: list[tuple[int, str, dict]] = []

    while iterations < cfg.max_iterations:
        iterations += 1
        for position, tool_name, payload in previous_round:
            messages[position] = ToolMessage(
                tool_call_id=messages[position].tool_call_id,
                content=json.dumps(digest(payload, tool=tool_name), default=str),
            )
        previous_round, this_round = this_round, []

        # Snapshot what the agent had before this round, so "did anything
        # useful happen?" is answerable at the end of it.
        before = (len(evidence), len(findings), len(proposals))
        try:
            with Timer() as timer:
                response: AIMessage = await invoke_llm(
                    model, messages, calls_so_far=llm_calls
                )
        except LLMBudgetExceeded as exc:
            stop_reason = str(exc)
            seq = record_step(
                state, f"worker:{worker.value}", "budget_exceeded",
                {"iteration": iterations, "llm_calls": llm_calls}, seq=seq,
            )
            break
        llm_calls += 1
        messages.append(response)

        tool_names = [c["name"] for c in (response.tool_calls or [])]
        seq = record_step(
            state,
            f"worker:{worker.value}",
            "done" if not tool_names else "ok",
            {
                "iteration": iterations,
                "tool_calls": tool_names,
                **(
                    {"reason": "agent stopped calling tools"}
                    if not tool_names
                    else {"reason": "requested tools"}
                ),
            },
            timer.elapsed_ms,
            seq=seq,
        )
        if not tool_names:
            break

        for call in response.tool_calls:
            name = call["name"]
            args = call.get("args") or {}
            signature = f"{name}:{json.dumps(args, sort_keys=True, default=str)}"

            if signature in seen_calls:
                messages.append(
                    ToolMessage(
                        tool_call_id=call["id"],
                        content=json.dumps(
                            {
                                "error": True,
                                "reason": "duplicate_call",
                                "message": (
                                    "You already made this exact call. Its result "
                                    "has not changed. Use what you have, or ask "
                                    "something different."
                                ),
                            }
                        ),
                    )
                )
                continue
            seen_calls.add(signature)

            with Timer() as call_timer:
                try:
                    raw = await session.call(name, args)
                    result = _parse(raw)
                    status = "error" if result.get("error") else "ok"
                    if status == "ok":
                        ingested = _ingest(result, tool=name)
                except MalformedToolResult as exc:
                    # The tool succeeded but handed back something that does not
                    # match its contract. Treated as a tool error so the agent
                    # can react, and audited because it means a server-side bug.
                    result = {
                        "error": True,
                        "reason": "malformed_tool_result",
                        "message": str(exc),
                    }
                    status = "error"
                    record_audit(
                        state,
                        AuditEventType.TOOL_ERROR,
                        f"{name} returned a malformed payload",
                        {"args": args, "detail": str(exc)},
                    )
                except Exception as exc:  # tool transport or unexpected failure
                    result = {
                        "error": True,
                        "reason": "tool_failure",
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                    status = "error"

            if status == "error":
                errors.append(f"{name}: {result.get('message')}")
                if result.get("reason") == "cross_tenant":
                    record_audit(
                        state,
                        AuditEventType.TENANT_VIOLATION,
                        f"Agent attempted cross-tenant access via {name}",
                        {"args": args, "message": result.get("message")},
                    )
            else:
                # Dumped back to plain dicts: validated on the way in, but state
                # has to stay JSON-serialisable for the checkpointer.
                for record in ingested.evidence:
                    evidence[record.evidence_id] = record.model_dump(mode="json")
                # Persisted as it arrives rather than at the end of the turn, so
                # a citation survives a turn that later fails or is abandoned at
                # the approval gate.
                persist_evidence(state, ingested.evidence)
                findings.extend(f.model_dump(mode="json") for f in ingested.findings)
                proposals.extend(a.model_dump(mode="json") for a in ingested.proposals)
                if name == "get_device_history" and ingested.points:
                    # The only source a trend_line chart may draw from — a full
                    # series, not just the anchor points kept in the evidence
                    # ledger. Keyed to match exactly how a chart request will
                    # reference it (device_id + the metric name the model used).
                    key = f"{args.get('device_id')}::{args.get('metric')}"
                    history_series[key] = [
                        p.model_dump(mode="json") for p in ingested.points
                    ]

            call_log.append(
                {
                    "tool": name,
                    "args": args,
                    "status": status,
                    "duration_ms": call_timer.elapsed_ms,
                    "result_summary": _summarise(result),
                }
            )
            seq = record_step(
                state,
                f"{worker.value}/{name}",
                status,
                {"args": args, "summary": _summarise(result)},
                call_timer.elapsed_ms,
                seq=seq,
                attach_llm_usage=False,
            )
            # The ledger already holds the full record (``_ingest`` ran above),
            # so the conversation gets the slimmed copy — see tool_payload.
            messages.append(
                ToolMessage(
                    tool_call_id=call["id"],
                    content=json.dumps(trim_for_model(result), default=str)[:20000],
                )
            )
            this_round.append((len(messages) - 1, name, result))

            consecutive_errors = consecutive_errors + 1 if status == "error" else 0

        # -- circuit breakers -------------------------------------------
        # Every recent call failed. Continuing spends the remaining budget on
        # a route that is not working; the agent should report what it has.
        if consecutive_errors >= settings.max_consecutive_tool_errors:
            stop_reason = f"{consecutive_errors} consecutive tool errors"
            seq = record_step(
                state, f"worker:{worker.value}", "circuit_broken",
                {"iteration": iterations, "reason": stop_reason}, seq=seq,
            )
            break

        # Calls succeeded but produced nothing new. Duplicate detection catches
        # an identical repeat; this catches the subtler case of an agent trying
        # different calls that all return nothing it can use.
        if (len(evidence), len(findings), len(proposals)) == before:
            unproductive += 1
            if unproductive >= settings.max_unproductive_iterations:
                stop_reason = f"{unproductive} rounds produced no new results"
                seq = record_step(
                    state, f"worker:{worker.value}", "no_progress",
                    {"iteration": iterations, "reason": stop_reason}, seq=seq,
                )
                break
        else:
            unproductive = 0
    else:
        seq = record_step(
            state,
            f"worker:{worker.value}",
            "truncated",
            {
                "iteration": iterations,
                "reason": f"hit this agent's budget of {cfg.max_iterations} iterations",
            },
            seq=seq,
        )

    update: dict = {
        "evidence": evidence,
        "findings": findings,
        "proposals": proposals,
        "tool_calls": call_log,
        "tool_errors": errors,
        "tool_iterations": state.tool_iterations + iterations,
        "history_series": history_series,
        "seen_tool_calls": sorted(seen_calls),
        "dispatch_index": index + 1,
        "llm_calls": llm_calls,
        "step_seq": seq,
    }
    if stop_reason:
        errors.append(f"{worker.value}: stopped early — {stop_reason}")
        update["tool_errors"] = errors

    # Record the question once per turn, not once per worker.
    if index == 0:
        update["messages"] = [HumanMessage(content=state.question)]

    # Nothing retrieved by any worker so far and this one only produced errors:
    # there is no basis for an answer. A later worker legitimately produces no
    # evidence of its own (action_agent never does), so this only fires while
    # the ledger is still empty.
    if not evidence and errors:
        cross_tenant = any("company" in e.lower() or "visible in" in e.lower() for e in errors)
        update["refusal_reason"] = "cross_tenant" if cross_tenant else "tool_failure"
        update["refusal_message"] = (
            "I could not retrieve any telemetry for that request. " + errors[0]
        )
    return update


def _summarise(result: dict[str, Any]) -> dict[str, Any]:
    """Compact result description for the trace, avoiding huge payloads."""
    if result.get("error"):
        return {"error": result.get("reason"), "message": result.get("message")}
    summary: dict[str, Any] = {}
    for key in ("match_count", "finding_count", "device_count", "status"):
        if key in result:
            summary[key] = result[key]
    if result.get("evidence"):
        summary["evidence_count"] = len(result["evidence"])
    if result.get("note"):
        summary["note"] = result["note"]
    if result.get("action"):
        summary["action_id"] = result["action"].get("action_id")
    if result.get("points"):
        summary["points"] = len(result["points"])
    return summary or {"ok": True}


def route_after_worker(state: AgentState) -> str:
    """Loop to the next queued worker, or move on to grounding."""
    if state.refusal_reason:
        return "refuse"
    if state.dispatch_index < len(state.dispatch_queue):
        return "run_worker"
    return "ground"
