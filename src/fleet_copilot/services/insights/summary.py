"""A one-line, human-readable explanation for a finding.

Composed from the metrics the detectors already computed — no model, no new
data. This is what fills a finding's ``explanation``: the same reproducible
sentence shown in the Insights listing, the detail drawer's "Why", and exports.

Kept out of the detectors themselves so the phrasing lives in one place and a
finding type without a specific template still gets a sensible fallback.
"""
from __future__ import annotations

from ...domain.models import Finding


def summarise_finding(finding: Finding) -> str:
    """One sentence explaining why the finding was raised, from its metrics."""
    builder = _BUILDERS.get(finding.finding_type.value)
    text = builder(finding.metrics) if builder else None
    return text or _fallback(finding)


def _num(value, digits: int = 0):
    """Round for display, tolerating missing or non-numeric metrics."""
    if not isinstance(value, (int, float)):
        return value
    return round(value, digits) if digits else round(value)


def _battery(m: dict) -> str:
    parts = []
    if m.get("cycle_count") is not None:
        parts.append(f"{_num(m['cycle_count'])} charge cycles")
    if m.get("condition"):
        parts.append(f"condition '{m['condition']}'")
    if m.get("capacity_decline_pct"):
        parts.append(f"capacity down {_num(m['capacity_decline_pct'], 1)}%")
    readings = m.get("readings")
    tail = f" over {readings} readings" if readings else ""
    return "Battery shows " + ", ".join(parts) + tail + "." if parts else ""


def _disk(m: dict) -> str:
    current = m.get("current_free_pct")
    if current is None:
        return ""
    text = f"{_num(current, 1)}% free"
    first = m.get("first_free_pct")
    if first is not None and first != current:
        text += f", down from {_num(first, 1)}%"
    days = m.get("days_to_full")
    if days is not None:
        text += f"; about {_num(days, 1)} days to full at the current rate"
    return text + "."


def _ram(m: dict) -> str:
    mean = m.get("mean_used_pct")
    if mean is None:
        return ""
    text = f"Averaging {_num(mean, 1)}% memory use"
    if m.get("peak_used_pct") is not None:
        text += f" (peak {_num(m['peak_used_pct'], 1)}%)"
    breach, total = m.get("breach_snapshots"), m.get("total_snapshots")
    threshold = m.get("threshold_pct")
    if breach is not None and total:
        band = f" above {_num(threshold)}%" if threshold is not None else ""
        text += f", staying{band} in {breach} of {total} snapshots"
    return text + "."


def _compliance(m: dict) -> str:
    check = str(m.get("check_id", "a check")).replace("_", " ")
    status = m.get("current_status", "fail")
    text = f"The {check} check regressed to {status}"
    if m.get("recovered"):
        text += ", and has since recovered"
    return text + "."


def _software(m: dict) -> str:
    apps = m.get("applications") or []
    if not apps:
        return ""
    shown = ", ".join(str(a) for a in apps[:3])
    more = f" and {len(apps) - 3} more" if len(apps) > 3 else ""
    return f"{len(apps)} unapproved: {shown}{more}."


_BUILDERS = {
    "battery_eol": _battery,
    "disk_pressure": _disk,
    "ram_pressure": _ram,
    "compliance_drift": _compliance,
    "unapproved_software": _software,
}


def _fallback(finding: Finding) -> str:
    """Better than an empty cell for a type without a template."""
    metrics = ", ".join(
        f"{k.replace('_', ' ')} {v}"
        for k, v in list(finding.metrics.items())[:3]
        if not isinstance(v, (list, dict))
    )
    return f"{finding.title}." + (f" {metrics}." if metrics else "")
