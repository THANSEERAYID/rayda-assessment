"""Request and response models for the HTTP API."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..domain.charts import ChartData
from ..domain.models import CopilotResponse, Finding, ProposedAction


class CompanyOut(BaseModel):
    company_id: str
    name: str
    device_count: int


class StartThreadIn(BaseModel):
    company_id: str
    title: str | None = None


class ThreadOut(BaseModel):
    thread_id: str
    company_id: str
    title: str | None = None
    # Populated by the listing endpoint so a thread picker can show something
    # recognisable; absent when a single thread is returned from create.
    step_count: int | None = None
    last_activity: str | None = None


class MessageIn(BaseModel):
    """A turn.

    ``company_id`` is echoed from the tenant selector and checked against the
    thread's binding; it cannot be used to redirect an existing conversation.
    """

    thread_id: str
    company_id: str
    message: str = Field(min_length=1)
    # "chat" (default) or "task" — recorded with the turn so the Action-performed
    # view can list task-card investigations apart from chat questions.
    source: str = "chat"


class DecisionIn(BaseModel):
    action_id: str
    approved: bool
    note: str | None = None


class ApprovalIn(BaseModel):
    thread_id: str
    company_id: str
    decisions: list[DecisionIn]


class TurnOut(CopilotResponse):
    """The full turn result the UI renders."""


class PromptArgumentOut(BaseModel):
    name: str
    description: str | None = None
    required: bool = False


class PromptOut(BaseModel):
    name: str
    title: str
    description: str | None = None
    arguments: list[PromptArgumentOut] = Field(default_factory=list)


class PromptTextOut(BaseModel):
    name: str
    text: str


class InsightsOut(BaseModel):
    company_id: str
    window_days: int
    findings: list[Finding]
    detectors_available: list[str]


class InsightsTrendsOut(BaseModel):
    company_id: str
    window_days: int
    charts: list[ChartData]


class PendingActionsOut(BaseModel):
    company_id: str
    actions: list[ProposedAction]


class TraceStepOut(BaseModel):
    seq: int
    turn_id: str
    # Present on the company-wide listing so a run can name the conversation it
    # belongs to; omitted when the caller already asked for one thread.
    thread_id: str | None = None
    node: str
    status: str
    detail: dict[str, Any]
    duration_ms: int | None = None
    created_at: str


class TraceOut(BaseModel):
    thread_id: str | None = None
    company_id: str | None = None
    steps: list[TraceStepOut]


class AuditEventOut(BaseModel):
    id: int
    event_type: str
    actor: str
    summary: str
    detail: dict[str, Any]
    thread_id: str | None = None
    created_at: str


class AuditOut(BaseModel):
    company_id: str
    events: list[AuditEventOut]
