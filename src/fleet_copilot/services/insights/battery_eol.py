"""Batteries approaching end of life.

The telemetry has no ``design_capacity`` field, so the usual "health = current
capacity / design capacity" ratio cannot be computed. Three independent signals
are available instead, and this detector requires **at least two** of them before
reporting a device:

* ``condition`` reported as something other than "Normal" (the vendor's own
  verdict, e.g. "Service Recommended");
* a high ``cycle_count`` — laptop batteries are rated for roughly 1000 cycles;
* a measurable decline in ``full_charge_capacity`` across the window.

Requiring corroboration matters because each signal alone is weak: a high cycle
count on a healthy battery is normal for an older-but-fine machine, and a single
capacity reading can wobble. Devices with no battery hardware are skipped rather
than reported as unhealthy.
"""
from __future__ import annotations

from ...domain.enums import FindingType, Severity
from ...domain.models import Finding
from ...evidence.ledger import build_evidence
from .base import Detector, DetectorContext, DetectorOutput


class BatteryEndOfLifeDetector(Detector):
    finding_type = FindingType.BATTERY_EOL

    def run(self, ctx: DetectorContext) -> DetectorOutput:
        out = DetectorOutput()
        cfg = ctx.settings

        for device_id, rows in sorted(ctx.snapshots_by_device.items()):
            readings = [r for r in rows if r.battery_present]
            if not readings:
                # No battery hardware, or no reading in the window at all.
                continue

            latest = readings[-1]
            capacities = [
                float(r.battery_full_charge_capacity)
                for r in readings
                if r.battery_full_charge_capacity is not None
            ]
            conditions = {r.battery_condition for r in readings if r.battery_condition}
            cycle_count = latest.battery_cycle_count

            decline_pct = 0.0
            if len(capacities) >= 2 and capacities[0]:
                decline_pct = round(
                    100.0 * (capacities[0] - capacities[-1]) / capacities[0], 2
                )

            flagged_condition = bool(conditions - {"Normal"})
            high_cycles = (
                cycle_count is not None and cycle_count >= cfg.battery_high_cycle_count
            )
            declining = decline_pct >= cfg.battery_capacity_decline_pct

            signals = {
                "condition": flagged_condition,
                "cycle_count": high_cycles,
                "capacity_decline": declining,
            }
            triggered = [name for name, hit in signals.items() if hit]
            if len(triggered) < 2:
                continue

            severity = Severity.HIGH if len(triggered) == 3 else Severity.MEDIUM
            reported_condition = next(
                (c for c in sorted(conditions) if c != "Normal"), latest.battery_condition
            )

            records = [
                build_evidence(
                    tool=self.tool_name,
                    field="battery.condition",
                    value=reported_condition,
                    device_id=device_id,
                    device_label=ctx.label(device_id),
                    snapshot_ts=latest.collected_at,
                    detail={"cycle_count": cycle_count},
                ),
                build_evidence(
                    tool=self.tool_name,
                    field="battery.cycle_count",
                    value=cycle_count,
                    device_id=device_id,
                    device_label=ctx.label(device_id),
                    snapshot_ts=latest.collected_at,
                    detail={"threshold": cfg.battery_high_cycle_count},
                ),
            ]
            if capacities:
                records.append(
                    build_evidence(
                        tool=self.tool_name,
                        field="battery.full_charge_capacity",
                        value=capacities[-1],
                        device_id=device_id,
                        device_label=ctx.label(device_id),
                        snapshot_ts=latest.collected_at,
                        detail={
                            "first_capacity": capacities[0],
                            "decline_pct": decline_pct,
                        },
                    )
                )
            out.evidence.extend(records)

            out.findings.append(
                Finding(
                    finding_type=self.finding_type,
                    device_id=device_id,
                    device_label=ctx.label(device_id),
                    company_id=ctx.company_id,
                    severity=severity,
                    title="Battery is near end of life",
                    metrics={
                        "cycle_count": cycle_count,
                        "condition": reported_condition,
                        "capacity_decline_pct": decline_pct,
                        "first_capacity": capacities[0] if capacities else None,
                        "last_capacity": capacities[-1] if capacities else None,
                        "signals_triggered": triggered,
                        "readings": len(readings),
                    },
                    evidence_ids=[r.evidence_id for r in records],
                )
            )
        return out
