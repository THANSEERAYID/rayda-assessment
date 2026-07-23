"""Domain exceptions.

These are raised by the service and MCP layers and are translated into typed
refusals by the agent, so the evaluation suite can assert on *why* something was
refused rather than pattern-matching prose.
"""
from __future__ import annotations

from .enums import RefusalReason


class FleetCopilotError(Exception):
    """Base class. Carries the refusal reason the agent should surface."""

    reason: RefusalReason = RefusalReason.TOOL_FAILURE

    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self.message = message
        self.context = context


class TenantViolation(FleetCopilotError):
    """A tool call tried to reach data outside the session's bound tenant.

    Raised *before* any query runs. Always written to the audit log — a silent
    empty result would hide the attempt from the operator.
    """

    reason = RefusalReason.CROSS_TENANT


class InsufficientEvidence(FleetCopilotError):
    """An action was proposed without resolvable supporting evidence."""

    reason = RefusalReason.INSUFFICIENT_EVIDENCE


class UnknownEntity(FleetCopilotError):
    """A device/employee id does not exist at all (in any tenant).

    Deliberately distinct from :class:`TenantViolation`: reporting "not found"
    for a device that exists in another tenant would leak its existence.
    Callers must map both to the same user-visible message.
    """

    reason = RefusalReason.UNANSWERABLE_FROM_DATA


class UngroundedClaim(FleetCopilotError):
    """A model claim cited evidence that does not exist or does not match."""

    reason = RefusalReason.UNGROUNDED_CLAIMS


class ToolFailure(FleetCopilotError):
    reason = RefusalReason.TOOL_FAILURE
