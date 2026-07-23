"""Shared helpers for graph nodes."""
from __future__ import annotations

import time
from functools import lru_cache, wraps
from pathlib import Path
from typing import Any, Awaitable, Callable

from langchain_core.runnables import RunnableConfig

from ...domain.enums import AuditEventType
from ...domain.models import Evidence
from ...domain.text import format_timestamp, sanitize_for_prompt
from ...storage.db import connect
from ...storage.repositories.audit import AuditRepository, RunTraceRepository
from ...storage.repositories.evidence import EvidenceRepository
from ..llm import take_llm_usage
from ..mcp_client import ToolSession
from ..progress import describe_step, publish
from ..state import STATE_FIELDS, AgentState

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"

# Rules every worker obeys regardless of its role. Kept here and composed onto
# each role prompt rather than copied into three files, so a change to the
# tenant rule or the empty-result rule cannot land in one agent and miss another.
SHARED_WORKER_PREAMBLE = """\
You are one specialized agent in an IT fleet management copilot. A manager agent
dispatched this step to you.

- The company is fixed by the interface. Never pass `company_id` to a tool — it
  is treated as attempted cross-tenant access, rejected and logged.
- Call tools for facts. Do not guess, estimate, or rely on prior knowledge about
  these devices.
- An empty result is an answer: nothing matches, the query did not fail. Do not
  repeat the same call hoping for a different result, and do not broaden the
  criteria until something turns up.
- On `error: true`, read the message and correct your arguments. On
  `cross_tenant`, stop — do not look for another route to that data.
- You have only your role's tools. If the task needs something else, stop and say
  so rather than approximating it.
- Devices have a serial (`device_id`, e.g. `MT7PJB7N5LRE`) and a readable name
  (e.g. `acme-macbook-4 (MacBook Pro)`). Tools take the serial; anything a person
  reads uses the name.
- Stop calling tools as soon as your part is done. A later step writes the reply
  — do not write prose now."""


@lru_cache(maxsize=16)
def load_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")


@lru_cache(maxsize=16)
def load_worker_prompt(name: str) -> str:
    """A worker's system prompt: shared rules plus its role-specific section."""
    return f"{SHARED_WORKER_PREAMBLE}\n\n{load_prompt(name)}"


NodeFn = Callable[[AgentState, RunnableConfig], Awaitable[dict[str, Any] | None]]


def validated_node(fn: NodeFn) -> NodeFn:
    """Reject state updates that name a field the schema does not have.

    LangGraph applies a node's update dict without validating it against a
    Pydantic state schema — an unknown key is silently dropped and a wrong type
    passes through untouched. A misspelled field would therefore vanish without
    complaint and surface later as a confusing absence, which is exactly the
    class of bug the schema was meant to prevent.

    Wrapping each node closes that gap where it can be closed cheaply: writes go
    through here, and reads are already protected by attribute access on the
    model.
    """

    @wraps(fn)
    async def wrapper(state: AgentState, config: RunnableConfig):
        update = await fn(state, config)
        if update:
            unknown = set(update) - STATE_FIELDS
            if unknown:
                raise ValueError(
                    f"Node '{fn.__name__}' returned unknown state field(s): "
                    f"{', '.join(sorted(unknown))}. "
                    f"Valid fields: {', '.join(sorted(STATE_FIELDS))}"
                )
        return update

    return wrapper


def tool_session(config: RunnableConfig) -> ToolSession:
    """The MCP session for this invocation.

    Injected through ``configurable`` rather than captured in a closure so the
    compiled graph — and its checkpointer — can be built once and reused across
    requests that each have their own tool server process.
    """
    session = (config.get("configurable") or {}).get("tool_session")
    if session is None:
        raise RuntimeError("No MCP tool session was provided for this invocation.")
    return session


def record_step(
    state: AgentState,
    node: str,
    status: str,
    detail: dict[str, Any] | None = None,
    duration_ms: int | None = None,
    *,
    seq: int | None = None,
    attach_llm_usage: bool = True,
    once: bool = False,
) -> int:
    """Append a trace row. Returns the sequence number used.

    Pass ``seq`` when a node emits several steps before returning — the state it
    holds is still the one it was called with, so its ``step_seq`` would repeat.

    Pass ``once`` from a node that calls ``interrupt()``. LangGraph resumes such
    a node by re-running it from the top, so a step recorded above that call is
    written twice for one real pause — see ``RunTraceRepository.has_step``.

    When ``attach_llm_usage`` is true (the default), any usage stashed by the
    most recent :func:`invoke_llm` call is folded into ``detail.llm`` and then
    cleared, so the next step does not inherit it.

    Tracing writes in its own transaction so a step is recorded even when the
    node it describes goes on to fail.
    """
    next_seq = (state.step_seq if seq is None else seq) + 1
    payload = dict(detail or {})
    if attach_llm_usage:
        usage = take_llm_usage()
        if usage:
            payload["llm"] = usage
    try:
        with connect() as conn:
            repo = RunTraceRepository(conn)
            if once and repo.has_step(
                thread_id=state.thread_id or "unknown",
                turn_id=state.turn_id or "unknown",
                node=node,
                seq=next_seq,
            ):
                return next_seq
            repo.record_step(
                thread_id=state.thread_id or "unknown",
                turn_id=state.turn_id or "unknown",
                seq=next_seq,
                node=node,
                status=status,
                detail=payload,
                duration_ms=duration_ms,
            )
    except Exception:  # pragma: no cover - tracing must never break a turn
        pass

    # The same step, narrated for anyone watching the turn happen. A no-op
    # unless a stream is attached, so the plain request/response path is
    # unchanged.
    phase, sentence = describe_step(node, status, payload)
    publish(
        {
            "type": "step",
            "seq": next_seq,
            "node": node,
            "status": status,
            "phase": phase,
            "message": sentence,
            "duration_ms": duration_ms,
        }
    )
    return next_seq


def persist_evidence(state: AgentState, records: list[Evidence]) -> None:
    """Keep citable readings past the life of the turn.

    Grounding checks citations against the in-memory ledger while a turn runs;
    this is what lets the Approvals page resolve the same ids days later, when
    that ledger is long gone.

    Never allowed to break a turn — an unstored citation degrades the audit
    trail, a raised exception loses the answer.
    """
    if not records or not state.company_id:
        return
    try:
        with connect() as conn:
            EvidenceRepository(conn).record_many(
                records,
                company_id=state.company_id,
                thread_id=state.thread_id,
                turn_id=state.turn_id,
            )
    except Exception:  # pragma: no cover - bookkeeping must not fail a turn
        pass


def step_already_recorded(
    state: AgentState, node: str, *, seq: int | None = None
) -> bool:
    """Whether ``node``'s step for this turn is already on record.

    Lets a node that suspends on ``interrupt()`` tell a fresh pause from the
    replay that follows a resume, and so skip the side effects above the
    interrupt — an audit entry, a narration frame — that would otherwise be
    emitted twice for one decision.
    """
    next_seq = (state.step_seq if seq is None else seq) + 1
    try:
        with connect() as conn:
            return RunTraceRepository(conn).has_step(
                thread_id=state.thread_id or "unknown",
                turn_id=state.turn_id or "unknown",
                node=node,
                seq=next_seq,
            )
    except Exception:  # pragma: no cover - never break a turn over bookkeeping
        return False


def record_audit(
    state: AgentState,
    event_type: AuditEventType,
    summary: str,
    detail: dict[str, Any] | None = None,
    actor: str = "agent",
) -> None:
    try:
        with connect() as conn:
            AuditRepository(conn).record(
                event_type=event_type,
                company_id=state.company_id or None,
                thread_id=state.thread_id or None,
                actor=actor,
                summary=summary,
                detail=detail or {},
            )
    except Exception:  # pragma: no cover
        pass


def render_handoff(state: AgentState, limit: int = 120) -> str | None:
    """What a previous agent established, rendered for the next one.

    Without this a second worker starts blind: the evidence sits in state but
    never reaches its prompt, so an agent with no discovery tools of its own
    could not cite anything and every action it proposed would be refused for
    insufficient evidence.

    Returns ``None`` for the first worker in a dispatch, which has nothing
    handed to it.
    """
    findings = state.findings
    evidence = state.evidence
    proposals = state.proposals
    if not (findings or evidence or proposals):
        return None

    parts: list[str] = ["## Handed over by the previous agent", ""]

    if findings:
        parts.append("Findings (figures already computed — use them as given):")
        for finding in findings[:40]:
            # title and metrics carry telemetry-derived strings (software
            # names, model names), so they are neutralised like evidence values.
            parts.append(
                f"- {finding['finding_type']} on "
                f"{sanitize_for_prompt(finding.get('device_label') or finding['device_id'])} "
                f"[device_id {finding['device_id']}] "
                f"[{finding['severity']}]: {sanitize_for_prompt(finding['title'])} "
                f"metrics={sanitize_for_prompt(finding.get('metrics'), 400)} "
                f"evidence={finding.get('evidence_ids')}"
            )
        parts.append("")

    if evidence:
        parts.append(
            f"Evidence already gathered ({len(evidence)} records) — cite these ids:"
        )
        for record in list(evidence.values())[:limit]:
            where = sanitize_for_prompt(
                record.get("device_label") or record.get("device_id") or "fleet"
            )
            when = format_timestamp(record.get("snapshot_ts"))
            parts.append(
                f"[{record['evidence_id']}] {where} "
                f"{sanitize_for_prompt(record.get('field'))}="
                f"{sanitize_for_prompt(record.get('value'))} @ {when}"
            )
        if len(evidence) > limit:
            parts.append(f"... plus {len(evidence) - limit} more records.")
        parts.append("")

    if proposals:
        parts.append("Actions already proposed this turn:")
        for action in proposals:
            target = action.get("target_device_id") or action.get("target_employee_id")
            parts.append(f"- {action['action_id']}: {action['action_type']} on {target}")
        parts.append("")

    return "\n".join(parts).rstrip()


class Timer:
    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self.elapsed_ms = int((time.perf_counter() - self._start) * 1000)

    elapsed_ms: int = 0
