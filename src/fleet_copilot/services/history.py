"""Time-series retrieval — backs ``get_device_history``.

Series carry a small set of precomputed summary statistics (first, last, min,
max, slope) so the agent can describe a trend without doing arithmetic itself.
Anything the model would otherwise have to calculate is calculated here, where
it is testable.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection

from ..domain.enums import Metric
from ..domain.models import Evidence
from ..domain.text import format_device
from ..evidence.ledger import build_evidence
from ..storage.repositories.snapshots import SnapshotRepository

TOOL_HISTORY = "get_device_history"

_COLUMN_FOR_METRIC = {
    Metric.DISK_FREE_PCT: "disk_free_pct",
    Metric.RAM_USED_PCT: "ram_used_pct",
    Metric.BATTERY_PERCENTAGE: "battery_percentage",
    Metric.BATTERY_CYCLE_COUNT: "battery_cycle_count",
    Metric.BATTERY_FULL_CHARGE_CAPACITY: "battery_full_charge_capacity",
    Metric.BATTERY_CONDITION: "battery_condition",
}


class SeriesPoint(BaseModel):
    collected_at: str
    value: Any


class SeriesSummary(BaseModel):
    first: Any = None
    last: Any = None
    minimum: float | None = None
    maximum: float | None = None
    mean: float | None = None
    change: float | None = None
    change_pct: float | None = None
    slope_per_day: float | None = None
    points: int = 0
    missing_points: int = 0


class HistoryResult(BaseModel):
    device_id: str
    metric: str
    window_days: int
    points: list[SeriesPoint] = Field(default_factory=list)
    summary: SeriesSummary = Field(default_factory=SeriesSummary)
    evidence: list[Evidence] = Field(default_factory=list)
    note: str | None = None


class HistoryService:
    def __init__(self, conn: Connection, company_id: str) -> None:
        self.conn = conn
        self.company_id = company_id
        self.snapshots = SnapshotRepository(conn)

    def get_history(
        self, device_id: str, metric: Metric, *, window_days: int = 30
    ) -> HistoryResult:
        rows = self.snapshots.history(
            self.company_id, device_id, window_days=window_days
        )
        column = _COLUMN_FOR_METRIC[metric]

        points: list[SeriesPoint] = []
        numeric: list[float] = []
        missing = 0
        for row in rows:
            value = getattr(row, column)
            if value is None:
                missing += 1
            else:
                points.append(
                    SeriesPoint(collected_at=row.collected_at.isoformat(), value=value)
                )
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    numeric.append(float(value))

        label = (
            format_device(rows[0].hostname, rows[0].model_name) if rows else None
        )
        summary = self._summarise(numeric, points, missing)
        note = None
        if not rows:
            note = "No telemetry for that device in the requested window."
        elif missing and not points:
            note = (
                f"No readings for {metric.value} on this device — the hardware "
                "does not report it (for example, a desktop has no battery)."
            )
        elif missing:
            note = f"{missing} of {len(rows)} snapshots had no reading for this metric."

        evidence = self._evidence_for(device_id, metric, points, summary, label)
        return HistoryResult(
            device_id=device_id,
            metric=metric.value,
            window_days=window_days,
            points=points,
            summary=summary,
            evidence=evidence,
            note=note,
        )

    @staticmethod
    def _summarise(
        numeric: list[float], points: list[SeriesPoint], missing: int
    ) -> SeriesSummary:
        summary = SeriesSummary(points=len(points), missing_points=missing)
        if not points:
            return summary
        summary.first = points[0].value
        summary.last = points[-1].value
        if not numeric:
            return summary

        summary.minimum = min(numeric)
        summary.maximum = max(numeric)
        summary.mean = round(sum(numeric) / len(numeric), 4)
        summary.change = round(numeric[-1] - numeric[0], 4)
        if numeric[0]:
            summary.change_pct = round(100.0 * (numeric[-1] - numeric[0]) / numeric[0], 4)
        # Least-squares slope over the sample index. Snapshots are daily and
        # evenly spaced in this dataset, so index and day are interchangeable.
        n = len(numeric)
        if n > 1:
            mean_x = (n - 1) / 2
            mean_y = sum(numeric) / n
            denominator = sum((i - mean_x) ** 2 for i in range(n))
            if denominator:
                numerator = sum((i - mean_x) * (numeric[i] - mean_y) for i in range(n))
                summary.slope_per_day = round(numerator / denominator, 4)
        return summary

    @staticmethod
    def _evidence_for(
        device_id: str,
        metric: Metric,
        points: list[SeriesPoint],
        summary: SeriesSummary,
        label: str | None = None,
    ) -> list[Evidence]:
        """Cite the endpoints and the extreme — enough to support a trend claim.

        Emitting one record per point would flood the ledger with 30 near-identical
        citations per device without making any claim better supported.
        """
        if not points:
            return []
        records: list[Evidence] = []
        anchors = {"first": points[0], "last": points[-1]}
        for anchor, point in anchors.items():
            records.append(
                build_evidence(
                    tool=TOOL_HISTORY,
                    field=f"{metric.value}.{anchor}",
                    value=point.value,
                    device_id=device_id,
                    device_label=label,
                    snapshot_ts=point.collected_at,
                    detail={
                        "metric": metric.value,
                        "slope_per_day": summary.slope_per_day,
                        "change": summary.change,
                        "points": summary.points,
                    },
                )
            )
        if summary.minimum is not None:
            extreme = min(
                (p for p in points if isinstance(p.value, (int, float))),
                key=lambda p: p.value,
                default=None,
            )
            if extreme is not None:
                records.append(
                    build_evidence(
                        tool=TOOL_HISTORY,
                        field=f"{metric.value}.minimum",
                        value=extreme.value,
                        device_id=device_id,
                        device_label=label,
                        snapshot_ts=extreme.collected_at,
                        detail={"metric": metric.value, "maximum": summary.maximum},
                    )
                )
        return records
