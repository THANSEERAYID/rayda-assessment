"""Reusable workflow prompts exposed over MCP.

These are the *user-controlled* MCP primitive: an administrator picks one, rather
than the model deciding to invoke it. They encode the recurring fleet-management
questions worth asking, so the phrasing that reliably produces a well-grounded
answer lives in the server next to the tools it exercises, instead of being
retyped or hardcoded into one particular client.

Each returns the text of a question for the copilot to answer — the agent's own
planner, manager and worker prompts are separate and stay agent-side.
"""
from __future__ import annotations

from .context import get_context


def _tenant() -> str:
    """The bound company, so a template can name it rather than say "this fleet"."""
    return get_context().company_id


def fleet_health_review(window_days: int = 30) -> str:
    """A full health review: every detector, grouped by severity, with next steps.

    Use as a recurring check — weekly or before a maintenance window.
    """
    return (
        f"Give me a health review of the fleet over the last {window_days} days.\n\n"
        "Run a full insight scan and cover every category it reports: storage "
        "pressure, memory pressure, battery end-of-life, compliance drift and "
        "unapproved software.\n\n"
        "Group the findings by severity, highest first. For each one, state the "
        "device, the specific figures behind it, and what you would do about it. "
        "If a category has no findings, say so explicitly rather than omitting it "
        "— knowing something is clear is as useful as knowing it is not.\n\n"
        "Do not propose any actions yet; I want the picture first."
    )


def compliance_audit(severity: str = "all") -> str:
    """Current compliance posture, plus which devices have regressed.

    ``severity`` filters to low/medium/high, or "all" for everything.
    """
    scope = (
        "every severity level"
        if severity == "all"
        else f"checks at {severity} severity"
    )
    return (
        f"Audit compliance for {_tenant()}, covering {scope}.\n\n"
        "Report the current pass/fail state per check, then separately identify "
        "any device whose compliance has regressed during the window — something "
        "that was passing and now is not.\n\n"
        "Be precise about a check with no failures: state plainly that nothing is "
        "failing it. Do not go looking for an adjacent problem to report instead."
    )


def hardware_refresh_candidates() -> str:
    """Which devices are genuinely near end of life, and why.

    Distinguishes hardware that needs replacing from conditions that can be fixed.
    """
    return (
        "Which devices are candidates for hardware refresh?\n\n"
        "Look for end-of-life indicators — battery condition, cycle count and "
        "capacity decline — and sustained resource constraints that the hardware "
        "itself cannot resolve.\n\n"
        "Be careful to separate hardware at end of life from a fixable condition: "
        "a battery that is worn out warrants replacement, a full disk does not. "
        "For each candidate, give the evidence that justifies replacing it rather "
        "than remediating it."
    )


def device_deep_dive(device_id: str) -> str:
    """Everything known about one device: current state and 30-day trends."""
    return (
        f"Give me a full picture of device {device_id}.\n\n"
        "Cover its current state — hardware, OS version, storage, memory, battery "
        "and compliance — and how its storage and memory have trended over the "
        "last 30 days.\n\n"
        "Call out anything that needs attention, and say explicitly if nothing "
        "does."
    )


def storage_pressure_triage(free_pct_threshold: float = 15.0) -> str:
    """Devices low on disk now, plus those trending toward full."""
    return (
        f"Which devices are under storage pressure, using {free_pct_threshold}% "
        "free as the threshold?\n\n"
        "Separate devices that are critically low right now from ones that still "
        "look acceptable but are trending toward full. For the trending ones, "
        "include the rate of decline and how long they have before they run out.\n\n"
        "Order the results by urgency."
    )


def unapproved_software_report() -> str:
    """Which devices are running software outside the approved list."""
    return (
        f"Which devices in {_tenant()} are running unapproved software?\n\n"
        "For each one, name the application, its version, and the employee the "
        "device is assigned to. If nothing unapproved is installed anywhere, say "
        "so directly."
    )


PROMPTS = (
    fleet_health_review,
    compliance_audit,
    hardware_refresh_candidates,
    device_deep_dive,
    storage_pressure_triage,
    unapproved_software_report,
)
