"""Core domain models.

Pure data + pure functions. No I/O, no SQL, no LLM — which is what lets the bulk
of the evaluation suite run with neither a database server nor an API key.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .charts import ChartData, ChartRequest
from .text import format_timestamp, sanitize_for_prompt
from .enums import (
    ActionStatus,
    ActionType,
    ComplianceStatus,
    FindingType,
    RefusalReason,
    Severity,
)


# --------------------------------------------------------------------------
# Telemetry
# --------------------------------------------------------------------------
class ComplianceCheck(BaseModel):
    check_id: str
    status: ComplianceStatus
    severity: Severity


class InstalledSoftware(BaseModel):
    name: str
    version: str
    publisher: str


class DeviceSnapshot(BaseModel):
    """One point-in-time telemetry record, normalised across platforms.

    ``raw`` keeps the original JSON so a citation can always be resolved back to
    the exact source record the agent saw.
    """

    device_id: str
    company_id: str
    employee_id: str
    collected_at: datetime

    platform: str
    os_product_name: str
    os_product_version: str
    model_name: str
    hostname: str

    ram_total_bytes: int
    ram_used_bytes: int
    disk_size_bytes: int
    disk_available_bytes: int
    disk_encrypted: bool
    disk_mount_point: str

    # Absent on desktops (no battery hardware) and on some laptop snapshots
    # (a dropped reading). ``battery_present`` distinguishes the two.
    battery_present: bool = False
    battery_percentage: int | None = None
    battery_condition: str | None = None
    battery_cycle_count: int | None = None
    battery_full_charge_capacity: int | None = None
    battery_charging_status: str | None = None

    compliance: list[ComplianceCheck] = Field(default_factory=list)
    software: list[InstalledSoftware] = Field(default_factory=list)

    raw: dict[str, Any] = Field(default_factory=dict, repr=False)

    @property
    def disk_free_pct(self) -> float:
        if not self.disk_size_bytes:
            return 0.0
        return 100.0 * self.disk_available_bytes / self.disk_size_bytes

    @property
    def ram_used_pct(self) -> float:
        if not self.ram_total_bytes:
            return 0.0
        return 100.0 * self.ram_used_bytes / self.ram_total_bytes


class Device(BaseModel):
    """Identity of a device, independent of any single snapshot."""

    device_id: str
    company_id: str
    employee_id: str
    model_name: str
    platform: str
    hostname: str


# --------------------------------------------------------------------------
# Grounding
# --------------------------------------------------------------------------
class Evidence(BaseModel):
    """A single citable fact, recorded by the tool executor.

    ``evidence_id`` is assigned by the executor, never by the model. The model
    may only cite ids that already exist in the run's ledger, which is what turns
    grounding from an assertion into a foreign-key check.
    """

    evidence_id: str
    tool: str
    device_id: str | None = None
    # How the device is named in text a human reads. Presentational only — it is
    # deliberately not part of the evidence id, so a hostname change cannot
    # silently invalidate a citation.
    device_label: str | None = None
    snapshot_ts: datetime | None = None
    field: str
    value: Any
    detail: dict[str, Any] = Field(default_factory=dict)

    def summary(self) -> str:
        """One line for the prompt's evidence catalogue.

        The device id and value come from telemetry, which the endpoint controls,
        so both are neutralised — one record must stay on one line and cannot
        open what looks like a new prompt section.
        """
        where = sanitize_for_prompt(self.device_label or self.device_id or "fleet")
        # Formatted here so the model reads — and repeats — the same format the
        # rest of the product shows, rather than raw ISO.
        when = format_timestamp(self.snapshot_ts) if self.snapshot_ts else "n/a"
        return (
            f"[{self.evidence_id}] {where} "
            f"{sanitize_for_prompt(self.field)}={sanitize_for_prompt(self.value)} @ {when}"
        )


class Claim(BaseModel):
    """One assertion in an answer, with the evidence ids that support it."""

    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class GroundedAnswer(BaseModel):
    answer: str
    claims: list[Claim] = Field(default_factory=list)
    charts: list[ChartRequest] = Field(
        default_factory=list,
        description=(
            "0-3 charts that would help visualise this answer. Choose from the "
            "available catalogue (bar, pie, donut, data_table, severity_distribution, "
            "trend_line, stat_tile). Leave empty if no chart is appropriate."
        ),
    )


# --------------------------------------------------------------------------
# Insights
# --------------------------------------------------------------------------
class Finding(BaseModel):
    """A deterministic detector result.

    The numbers in ``metrics`` are computed in Python, never by the model; the
    model is only allowed to write ``explanation`` on top of them.
    """

    finding_type: FindingType
    device_id: str
    device_label: str | None = None
    company_id: str
    severity: Severity
    title: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    explanation: str | None = None


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------
class ProposedAction(BaseModel):
    """A state-changing action awaiting human approval.

    Created only with status ``PROPOSED``. Nothing in the agent path can move it
    to ``EXECUTED`` without a human decision arriving through the API.
    """

    action_id: str
    thread_id: str
    company_id: str
    action_type: ActionType
    target_device_id: str | None = None
    target_label: str | None = None
    target_employee_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    justification: str
    evidence_ids: list[str] = Field(default_factory=list)
    status: ActionStatus = ActionStatus.PROPOSED
    # Populated for the reviewer when a proposal is returned; not persisted.
    review: "ReviewSignal | None" = None
    created_at: datetime | None = None
    decided_at: datetime | None = None
    decided_by: str | None = None
    result: str | None = None


class ActionDecision(BaseModel):
    """A human's verdict on one proposed action."""

    action_id: str
    approved: bool
    note: str | None = None


# --------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------
class ReviewSignal(BaseModel):
    """How well-supported one proposal is, in facts rather than self-assessment.

    Attached for the approver's benefit; it never gates anything. Every action
    requires approval regardless of what this says.
    """

    evidence_count: int = 0
    distinct_fields: list[str] = Field(default_factory=list)
    supports_action_directly: bool = False
    review_priority: str = "check_carefully"
    notes: list[str] = Field(default_factory=list)


class AnswerQuality(BaseModel):
    """What the system observed while producing this turn's answer."""

    claims_kept: int = 0
    claims_rejected: int = 0
    grounding_retries: int = 0
    tool_errors: int = 0
    evidence_records: int = 0
    degraded: bool = False
    notes: list[str] = Field(default_factory=list)


class Refusal(BaseModel):
    reason: RefusalReason
    message: str


class CopilotResponse(BaseModel):
    """What the API returns for a turn."""

    thread_id: str
    company_id: str
    answer: str | None = None
    claims: list[Claim] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    charts: list[ChartData] = Field(default_factory=list)
    pending_actions: list[ProposedAction] = Field(default_factory=list)
    refusal: Refusal | None = None
    awaiting_approval: bool = False
    quality: AnswerQuality | None = None
