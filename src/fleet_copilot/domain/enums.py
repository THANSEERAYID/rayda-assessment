"""Enumerations shared across every layer.

Pure stdlib — this module must never import storage, services, or agent code.
"""
from __future__ import annotations

from enum import Enum


class Intent(str, Enum):
    """What the user is asking the copilot to do."""

    QA = "qa"
    INSIGHT = "insight"
    ACTION = "action"
    OUT_OF_SCOPE = "out_of_scope"


class AsOfMode(str, Enum):
    """Snapshot-selection semantics.

    Defined once here and implemented once in ``storage.repositories.snapshots``.
    Every telemetry question is ambiguous without this: 30 daily snapshots per
    device means "which devices are low on disk" could mean *now* or *ever*.
    """

    LATEST = "latest"  # newest snapshot per device (default for "which devices are ...")
    WINDOW = "window"  # every snapshot in the trailing window (trend questions)
    AT = "at"  # nearest snapshot at-or-before a timestamp


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ComplianceStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"


class Metric(str, Enum):
    """Time-series metrics exposed by ``get_device_history``."""

    DISK_FREE_PCT = "disk_free_pct"
    RAM_USED_PCT = "ram_used_pct"
    BATTERY_PERCENTAGE = "battery_percentage"
    BATTERY_CYCLE_COUNT = "battery_cycle_count"
    BATTERY_FULL_CHARGE_CAPACITY = "battery_full_charge_capacity"
    BATTERY_CONDITION = "battery_condition"


class FindingType(str, Enum):
    """Deterministic detector outputs."""

    BATTERY_EOL = "battery_eol"
    DISK_PRESSURE = "disk_pressure"
    RAM_PRESSURE = "ram_pressure"
    COMPLIANCE_DRIFT = "compliance_drift"
    UNAPPROVED_SOFTWARE = "unapproved_software"


class ActionType(str, Enum):
    CREATE_UPGRADE_ORDER = "create_upgrade_order"
    OPEN_REMEDIATION_TICKET = "open_remediation_ticket"
    FLAG_DEVICE_FOR_REPLACEMENT = "flag_device_for_replacement"
    NOTIFY_EMPLOYEE = "notify_employee"


class ActionStatus(str, Enum):
    """Lifecycle of a state-changing action.

    ``PROPOSED`` is the only status an LLM-driven tool call can create.
    The transition to ``APPROVED``/``EXECUTED`` requires a human decision
    delivered through the API.
    """

    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"


class RefusalReason(str, Enum):
    """Typed refusal codes, asserted on directly by the evaluation suite."""

    OUT_OF_SCOPE = "out_of_scope"
    UNANSWERABLE_FROM_DATA = "unanswerable_from_data"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CROSS_TENANT = "cross_tenant"
    TOOL_FAILURE = "tool_failure"
    UNGROUNDED_CLAIMS = "ungrounded_claims"


class AuditEventType(str, Enum):
    TOOL_CALL = "tool_call"
    TOOL_ERROR = "tool_error"
    TENANT_VIOLATION = "tenant_violation"
    ACTION_PROPOSED = "action_proposed"
    ACTION_APPROVED = "action_approved"
    ACTION_REJECTED = "action_rejected"
    ACTION_EXECUTED = "action_executed"
    GROUNDING_REJECTED = "grounding_rejected"
    REFUSAL = "refusal"
