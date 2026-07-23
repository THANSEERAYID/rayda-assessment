"""Detector framework.

Insights are computed in Python, not by the model. The model's only role is to
phrase an ``explanation`` on top of a :class:`Finding` whose numbers were already
derived here — which keeps trend arithmetic both correct and unit-testable.

All detectors share one :class:`DetectorContext` so the window is fetched once
rather than five times.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Sequence

from sqlalchemy.engine import Connection, Row

from ...config import Settings, settings as default_settings
from ...domain.enums import AsOfMode, FindingType
from ...domain.models import Evidence, Finding
from ...domain.text import format_device
from ...storage.repositories.compliance import ComplianceRepository
from ...storage.repositories.snapshots import SnapshotRepository
from ...storage.repositories.software import SoftwareRepository


@dataclass
class DetectorContext:
    conn: Connection
    company_id: str
    window_days: int
    settings: Settings
    snapshots_by_device: dict[str, list[Row]] = field(default_factory=dict)
    latest_by_device: dict[str, Row] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        conn: Connection,
        company_id: str,
        *,
        window_days: int = 30,
        settings: Settings | None = None,
    ) -> "DetectorContext":
        repo = SnapshotRepository(conn)
        window_rows = repo.select(
            company_id, mode=AsOfMode.WINDOW, window_days=window_days
        )
        by_device: dict[str, list[Row]] = {}
        for row in window_rows:
            by_device.setdefault(row.device_id, []).append(row)
        for rows in by_device.values():
            rows.sort(key=lambda r: r.collected_at)
        latest = {
            row.device_id: row
            for row in repo.select(company_id, mode=AsOfMode.LATEST)
        }
        return cls(
            conn=conn,
            company_id=company_id,
            window_days=window_days,
            settings=settings or default_settings,
            snapshots_by_device=by_device,
            latest_by_device=latest,
        )

    def label(self, device_id: str) -> str | None:
        """How this device is named in text a person reads."""
        row = self.latest_by_device.get(device_id)
        if row is None:
            rows = self.snapshots_by_device.get(device_id) or []
            row = rows[-1] if rows else None
        if row is None:
            return None
        return format_device(row.hostname, row.model_name)

    def compliance(self) -> ComplianceRepository:
        return ComplianceRepository(self.conn)

    def software(self) -> SoftwareRepository:
        return SoftwareRepository(self.conn)


@dataclass
class DetectorOutput:
    findings: list[Finding] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)

    def extend(self, other: "DetectorOutput") -> None:
        self.findings.extend(other.findings)
        self.evidence.extend(other.evidence)


class Detector(ABC):
    """One deterministic pattern over the telemetry window."""

    finding_type: FindingType
    tool_name: str = "run_insight_scan"

    @abstractmethod
    def run(self, ctx: DetectorContext) -> DetectorOutput:
        """Compute findings for one tenant."""


def slope_per_day(values: Sequence[float]) -> float | None:
    """Least-squares slope over sample index.

    Snapshots are daily and evenly spaced throughout this dataset, so the sample
    index is equivalent to elapsed days.
    """
    n = len(values)
    if n < 2:
        return None
    mean_x = (n - 1) / 2
    mean_y = sum(values) / n
    denominator = sum((i - mean_x) ** 2 for i in range(n))
    if not denominator:
        return None
    numerator = sum((i - mean_x) * (values[i] - mean_y) for i in range(n))
    return round(numerator / denominator, 4)
