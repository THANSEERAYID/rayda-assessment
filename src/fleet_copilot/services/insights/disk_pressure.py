"""Devices running out of storage.

Two distinct situations are worth telling an administrator apart, so this
detector reports which one applies:

``critical``
    Free space is below the critical threshold right now.

``trending``
    Free space is falling steadily enough to reach zero inside the projection
    horizon, even if today's figure still looks acceptable.

The projection uses the least-squares slope over the window rather than the
first-to-last difference, so one noisy reading cannot manufacture an alarm.
"""
from __future__ import annotations

from ...domain.enums import FindingType, Severity
from ...domain.models import Finding
from ...evidence.ledger import build_evidence
from .base import Detector, DetectorContext, DetectorOutput, slope_per_day

# Beyond this horizon a projection says more about noise than about storage.
_PROJECTION_HORIZON_DAYS = 60


class DiskPressureDetector(Detector):
    finding_type = FindingType.DISK_PRESSURE

    def run(self, ctx: DetectorContext) -> DetectorOutput:
        out = DetectorOutput()
        cfg = ctx.settings

        for device_id, rows in sorted(ctx.snapshots_by_device.items()):
            if not rows:
                continue
            series = [float(r.disk_free_pct) for r in rows]
            latest = rows[-1]
            current = series[-1]
            slope = slope_per_day(series) or 0.0

            days_to_full = None
            if slope < -0.01:
                days_to_full = round(current / abs(slope), 1)

            is_critical = current < cfg.disk_critical_free_pct
            is_low = current < cfg.disk_low_free_pct
            is_trending = (
                days_to_full is not None and days_to_full <= _PROJECTION_HORIZON_DAYS
            )
            if not (is_critical or is_low or is_trending):
                continue

            # The title states the condition only. Every surface that shows a
            # finding already identifies the device beside it, so repeating the
            # name here just makes the line longer to read.
            if is_critical:
                severity, pattern = Severity.HIGH, "critical"
                title = "Critically low on storage"
            elif is_low:
                severity, pattern = Severity.MEDIUM, "low"
                title = "Running low on storage"
            else:
                severity, pattern = Severity.MEDIUM, "trending"
                title = "Filling up — projected to run out of storage"

            records = [
                build_evidence(
                    tool=self.tool_name,
                    field="disk_free_pct",
                    value=round(current, 2),
                    device_id=device_id,
                    device_label=ctx.label(device_id),
                    snapshot_ts=latest.collected_at,
                    detail={
                        "available_bytes": latest.disk_available_bytes,
                        "size_bytes": latest.disk_size_bytes,
                        "threshold": cfg.disk_low_free_pct,
                    },
                ),
                build_evidence(
                    tool=self.tool_name,
                    field="disk_free_pct.first",
                    value=round(series[0], 2),
                    device_id=device_id,
                    device_label=ctx.label(device_id),
                    snapshot_ts=rows[0].collected_at,
                    detail={"slope_per_day": slope, "days_to_full": days_to_full},
                ),
            ]
            out.evidence.extend(records)

            out.findings.append(
                Finding(
                    finding_type=self.finding_type,
                    device_id=device_id,
                    device_label=ctx.label(device_id),
                    company_id=ctx.company_id,
                    severity=severity,
                    title=title,
                    metrics={
                        "pattern": pattern,
                        "current_free_pct": round(current, 2),
                        "first_free_pct": round(series[0], 2),
                        "min_free_pct": round(min(series), 2),
                        "slope_pct_per_day": slope,
                        "days_to_full": days_to_full,
                        "available_bytes": latest.disk_available_bytes,
                        "size_bytes": latest.disk_size_bytes,
                        "snapshots": len(series),
                    },
                    evidence_ids=[r.evidence_id for r in records],
                )
            )
        return out
