"""Fleet trend series for the dashboard overview.

Picks the highest-severity pressure findings and loads each device's metric
history so the UI can render standing ``trend_line`` charts without waiting for
a chat turn to call ``get_device_history``.
"""
from __future__ import annotations

from sqlalchemy.engine import Connection

from ...domain.charts import ChartData, ChartPoint, ChartType
from ...domain.enums import FindingType, Metric, Severity
from ...domain.models import Finding
from ...domain.text import unit_for_metric
from ..history import HistoryService

_SEVERITY_RANK = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}

# Which telemetry series to plot for each detector that has a natural trend.
_METRIC_FOR_FINDING: dict[FindingType, tuple[Metric, str]] = {
    FindingType.DISK_PRESSURE: (Metric.DISK_FREE_PCT, "Disk free %"),
    FindingType.RAM_PRESSURE: (Metric.RAM_USED_PCT, "RAM used %"),
    FindingType.BATTERY_EOL: (Metric.BATTERY_FULL_CHARGE_CAPACITY, "Battery capacity"),
}


def build_fleet_trends(
    conn: Connection,
    company_id: str,
    findings: list[Finding],
    *,
    window_days: int = 30,
    limit: int = 3,
) -> list[ChartData]:
    """Up to ``limit`` trend charts for the most urgent trendable findings."""
    candidates: list[tuple[int, Finding, Metric, str]] = []
    seen_devices: set[str] = set()
    for finding in findings:
        mapping = _METRIC_FOR_FINDING.get(finding.finding_type)
        if mapping is None:
            continue
        if finding.device_id in seen_devices:
            continue
        metric, label = mapping
        candidates.append(
            (_SEVERITY_RANK.get(finding.severity, 9), finding, metric, label)
        )
        seen_devices.add(finding.device_id)

    candidates.sort(key=lambda row: (row[0], row[1].finding_type.value, row[1].device_id))

    service = HistoryService(conn, company_id)
    charts: list[ChartData] = []
    for _, finding, metric, label in candidates[:limit]:
        history = service.get_history(
            finding.device_id, metric, window_days=window_days
        )
        numeric = [
            p
            for p in history.points
            if isinstance(p.value, (int, float)) and not isinstance(p.value, bool)
        ]
        if len(numeric) < 2:
            continue
        device = finding.device_label or finding.device_id
        unit = unit_for_metric(metric.value)
        charts.append(
            ChartData(
                chart_type=ChartType.TREND_LINE,
                title=f"{label} — {device}",
                unit=unit,
                points=[
                    ChartPoint(
                        label=p.collected_at,
                        device_id=finding.device_id,
                        value=float(p.value),
                        unit=unit,
                        evidence_id=None,
                        severity=finding.severity.value,
                    )
                    for p in numeric
                ],
                source_evidence_ids=list(finding.evidence_ids),
            )
        )
    return charts
