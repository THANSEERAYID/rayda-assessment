"""The chart catalog.

Charts follow the same grounding discipline as claims: the model picks *which*
chart and *what it's about*, but never authors the numbers that appear in it.
:class:`ChartRequest` is the model's structured output — a reference into data
that was already retrieved this turn. :class:`ChartData` is what the backend
resolves that reference into, built entirely from the evidence ledger, the
deterministic findings, or a captured history series — never from a value the
model wrote itself.

A request that cites evidence outside this turn's ledger, asks for a device's
history that was never actually fetched, or names an unknown metric is dropped,
mirroring how an ungrounded claim is dropped rather than patched.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ChartType(str, Enum):
    NONE = "none"
    DATA_TABLE = "data_table"
    BAR = "bar"
    PIE = "pie"
    DONUT = "donut"
    SEVERITY_DISTRIBUTION = "severity_distribution"
    TREND_LINE = "trend_line"
    STAT_TILE = "stat_tile"


# Prose catalogue injected into the grounder so the final-answer LLM knows
# exactly which visualisations it may request. Keep in sync with ChartType
# (minus ``none``) and the resolvers in ``chart_builder``.
CHART_CATALOGUE: list[tuple[str, str]] = [
    (
        "bar",
        "Magnitude across several devices for one numeric metric "
        "(e.g. disk-free % per device). Needs metric_field + evidence_ids.",
    ),
    (
        "pie",
        "Share of a whole — findings by type, severity mix for one finding_type, "
        "or categorical value shares from cited evidence (set metric_field). "
        "Prefer pie when composition matters more than ranking.",
    ),
    (
        "donut",
        "Same data shapes as pie, drawn as a ring with the total in the centre. "
        "Prefer donut when the headline total matters as much as the shares.",
    ),
    (
        "data_table",
        "Detailed row-per-device view when several fields matter at once. "
        "Needs evidence_ids.",
    ),
    (
        "severity_distribution",
        "Counts of findings by severity (horizontal bars). Optional finding_type "
        "filter. No evidence_ids needed.",
    ),
    (
        "trend_line",
        "One device's metric over time. Only if get_device_history was called "
        "this turn for that device_id + metric_field. Use for any trend question.",
    ),
    (
        "stat_tile",
        "A single headline number. Needs evidence_ids; optional metric_field.",
    ),
]


def describe_available_charts(
    *,
    findings_present: bool,
    history_keys: list[str],
    evidence_fields: list[str],
) -> str:
    """Human-readable chart menu for the final-answer LLM.

    Lists every chart type the UI can render, plus what this turn actually has
    available so the model does not request a trend with no history, etc.
    """
    lines = [
        "Available charts (propose 0–3 that fit the question; backend resolves "
        "values from retrieved data — never invent numbers):",
    ]
    for name, blurb in CHART_CATALOGUE:
        lines.append(f"- `{name}` — {blurb}")

    lines.append("")
    lines.append("This turn's chartable data:")
    if findings_present:
        lines.append(
            "- Detector findings are available → pie, donut, severity_distribution OK."
        )
    else:
        lines.append(
            "- No detector findings this turn → skip pie/donut/severity_distribution "
            "unless you can build a pie/donut from categorical evidence."
        )
    if history_keys:
        lines.append(
            "- History series retrieved (trend_line OK for these keys): "
            + ", ".join(history_keys[:12])
            + (" …" if len(history_keys) > 12 else "")
        )
    else:
        lines.append(
            "- No get_device_history results → do not request trend_line."
        )
    if evidence_fields:
        uniq = sorted(set(evidence_fields))[:24]
        lines.append(
            "- Evidence fields available for bar / pie / data_table / stat_tile: "
            + ", ".join(uniq)
            + (" …" if len(set(evidence_fields)) > 24 else "")
        )
    else:
        lines.append("- No device evidence fields available for metric charts.")
    return "\n".join(lines)


class ChartRequest(BaseModel):
    """What the model asks for. Every field here is a *reference*, not a value.

    ``evidence_ids`` reuses ids already cited in the answer's claims — a chart
    is never allowed to introduce evidence the answer itself didn't rely on.
    """

    chart_type: ChartType
    title: str = Field(description="Short chart title, e.g. 'Disk free % by device'")
    metric_field: str | None = Field(
        default=None,
        description=(
            "For 'bar': the evidence field to plot (must match a field on the "
            "cited evidence, e.g. 'disk_free_pct'). For 'trend_line': the exact "
            "metric name passed to get_device_history for this device this turn. "
            "For 'pie'/'donut': optional categorical evidence field whose value shares "
            "to plot; omit to build from findings instead."
        ),
    )
    device_id: str | None = Field(
        default=None, description="Required for 'trend_line'."
    )
    finding_type: str | None = Field(
        default=None,
        description=(
            "Optional filter for 'severity_distribution', 'pie', or 'donut', "
            "e.g. 'disk_pressure'."
        ),
    )
    evidence_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Evidence ids this chart is built from. Required for data_table, "
            "bar, stat_tile, and for pie/donut when metric_field is set."
        ),
    )
    stat_label: str | None = Field(
        default=None, description="Secondary label under a stat_tile's number."
    )


class ChartPoint(BaseModel):
    label: str
    # The serial behind the label. Charts display the readable name but a click
    # has to act on the identifier the tools accept.
    device_id: str | None = None
    value: float | int | str
    unit: str | None = None
    evidence_id: str | None = None
    severity: str | None = None


class ChartData(BaseModel):
    """The resolved, render-ready payload. Everything here traces to a source."""

    chart_type: ChartType
    title: str
    unit: str | None = None
    points: list[ChartPoint] = Field(default_factory=list)
    table_rows: list[dict[str, Any]] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    stat_value: float | int | str | None = None
    stat_label: str | None = None
    source_evidence_ids: list[str] = Field(default_factory=list)
