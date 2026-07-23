"""Devices persistently constrained by memory.

"Consistently constrained" is defined as a *share of the window* spent above the
high-utilisation threshold, not a single reading and not the mean. A one-off
spike during a build is normal; a machine above 85% on most days is short of RAM.

The dataset separates cleanly under this rule: two devices sit above the
threshold in every snapshot, two others exceed it only intermittently. The
sustained-ratio setting is what draws that line, and it is shared with the
evaluation suite through ``config`` so the two cannot drift apart.
"""
from __future__ import annotations

from ...domain.enums import FindingType, Severity
from ...domain.models import Finding
from ...evidence.ledger import build_evidence
from .base import Detector, DetectorContext, DetectorOutput


class RamPressureDetector(Detector):
    finding_type = FindingType.RAM_PRESSURE

    def run(self, ctx: DetectorContext) -> DetectorOutput:
        out = DetectorOutput()
        cfg = ctx.settings

        for device_id, rows in sorted(ctx.snapshots_by_device.items()):
            if not rows:
                continue
            series = [float(r.ram_used_pct) for r in rows]
            breaches = [v for v in series if v > cfg.ram_high_used_pct]
            ratio = len(breaches) / len(series)
            if ratio < cfg.ram_sustained_ratio:
                continue

            latest = rows[-1]
            peak_row = max(rows, key=lambda r: r.ram_used_pct)
            mean_used = round(sum(series) / len(series), 2)
            severity = Severity.HIGH if ratio >= 0.95 else Severity.MEDIUM

            records = [
                build_evidence(
                    tool=self.tool_name,
                    field="ram_used_pct",
                    value=round(series[-1], 2),
                    device_id=device_id,
                    device_label=ctx.label(device_id),
                    snapshot_ts=latest.collected_at,
                    detail={
                        "total_memory_bytes": latest.ram_total_bytes,
                        "threshold": cfg.ram_high_used_pct,
                        "mean_used_pct": mean_used,
                    },
                ),
                build_evidence(
                    tool=self.tool_name,
                    field="ram_used_pct.peak",
                    value=round(peak_row.ram_used_pct, 2),
                    device_id=device_id,
                    device_label=ctx.label(device_id),
                    snapshot_ts=peak_row.collected_at,
                    detail={
                        "breach_snapshots": len(breaches),
                        "total_snapshots": len(series),
                    },
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
                    title="Consistently short of memory",
                    metrics={
                        "breach_ratio": round(ratio, 3),
                        "breach_snapshots": len(breaches),
                        "total_snapshots": len(series),
                        "mean_used_pct": mean_used,
                        "peak_used_pct": round(max(series), 2),
                        "current_used_pct": round(series[-1], 2),
                        "threshold_pct": cfg.ram_high_used_pct,
                        "total_memory_bytes": latest.ram_total_bytes,
                    },
                    evidence_ids=[r.evidence_id for r in records],
                )
            )
        return out
