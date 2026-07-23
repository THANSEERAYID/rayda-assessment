"""Graph state.

Everything the agent learned during a turn lives here, which is what makes the
run replayable: the checkpointer persists this at every node boundary, so a turn
paused at the approval gate can resume unchanged hours later.

The schema is a Pydantic model rather than a ``TypedDict`` so the fields are
declared once with their defaults and types, and nodes read them as attributes —
``state.evidence`` raises on a typo where ``state.get("evidnce")`` would have
silently returned ``None``.

One caveat worth knowing: **LangGraph does not validate what a node returns**
against this schema. An unknown key in an update dict is silently discarded and
a wrong-typed value passes straight through, even with ``extra="forbid"``. So
reads are protected by attribute access, and writes are protected by the
``validated_node`` decorator in ``nodes/_common.py``, which checks every update
dict against :attr:`AgentState.model_fields` before it reaches the graph.
"""
from __future__ import annotations

from typing import Annotated, Any

from langgraph.graph.message import add_messages
from pydantic import BaseModel, ConfigDict, Field


class AgentState(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    # -- bound at turn start, never model-supplied -----------------------
    thread_id: str = ""
    turn_id: str = ""
    company_id: str = ""
    question: str = ""
    # Where the turn came from: "chat" or "task". Persisted with the turn so the
    # Action-performed view can list task investigations without pulling chat in.
    source: str = "chat"

    # -- conversation ----------------------------------------------------
    messages: Annotated[list, add_messages] = Field(default_factory=list)

    # -- planning --------------------------------------------------------
    intent: str = ""
    plan: list[str] = Field(default_factory=list)
    plan_rationale: str = ""

    # -- dispatch --------------------------------------------------------
    # The manager's ordered worker queue and how far through it we are. The
    # worker node self-loops until dispatch_index reaches the end.
    dispatch_queue: list[str] = Field(default_factory=list)
    dispatch_index: int = 0
    dispatch_reason: str = ""

    # -- retrieval -------------------------------------------------------
    # Mirror of the tool server's evidence ledger, keyed by evidence_id. Held as
    # plain dicts rather than Evidence models so the checkpointer can serialise
    # state without a custom encoder; nodes re-validate on the way out.
    evidence: dict[str, dict[str, Any]] = Field(default_factory=dict)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    tool_iterations: int = 0
    tool_errors: list[str] = Field(default_factory=list)
    # Every model call this turn has made, across all nodes. The per-worker
    # iteration caps bound each loop; this bounds their sum.
    llm_calls: int = 0
    # Call signatures already issued this turn. Held in state rather than per
    # node so deduplication spans workers — three tools appear in more than one
    # worker's roster, so a second agent could otherwise repeat the first's call.
    seen_tool_calls: list[str] = Field(default_factory=list)
    # Full point series from get_device_history calls this turn, keyed by
    # "device_id::metric" — the only source a trend_line chart may draw from.
    history_series: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)

    # -- grounding -------------------------------------------------------
    answer: str = ""
    claims: list[dict[str, Any]] = Field(default_factory=list)
    grounding_retries: int = 0
    rejected_claims: list[dict[str, Any]] = Field(default_factory=list)
    charts: list[dict[str, Any]] = Field(default_factory=list)
    rejected_charts: list[dict[str, Any]] = Field(default_factory=list)

    # -- actions ---------------------------------------------------------
    proposals: list[dict[str, Any]] = Field(default_factory=list)
    awaiting_approval: bool = False
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    executed: list[dict[str, Any]] = Field(default_factory=list)

    # -- outcome ---------------------------------------------------------
    refusal_reason: str = ""
    refusal_message: str = ""
    step_seq: int = 0


STATE_FIELDS: frozenset[str] = frozenset(AgentState.model_fields)


def new_state(
    *,
    thread_id: str,
    turn_id: str,
    company_id: str,
    question: str,
    source: str = "chat",
) -> AgentState:
    """A fresh turn. Every other field takes its declared default."""
    return AgentState(
        thread_id=thread_id,
        turn_id=turn_id,
        company_id=company_id,
        question=question,
        source=source,
    )
