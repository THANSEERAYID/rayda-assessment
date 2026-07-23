"""Compliance posture degrading over time.

A device that has *always* failed a check is a standing problem, already visible
in any point-in-time compliance report. Drift is different and more urgent: a
device that was passing and stopped. Something changed on it, and recently.

This detector therefore reports only pass-to-fail transitions, records when the
regression happened, and notes whether the device ever recovered.
"""
from __future__ import annotations

from ...domain.enums import ComplianceStatus, FindingType, Severity
from ...domain.models import Finding
from ...evidence.ledger import build_evidence
from .base import Detector, DetectorContext, DetectorOutput


class ComplianceDriftDetector(Detector):
    finding_type = FindingType.COMPLIANCE_DRIFT

    def run(self, ctx: DetectorContext) -> DetectorOutput:
        out = DetectorOutput()
        rows = ctx.compliance().series(
            ctx.company_id, window_days=ctx.window_days
        )

        series: dict[tuple[str, str], list] = {}
        for row in rows:
            series.setdefault((row.device_id, row.check_id), []).append(row)

        for (device_id, check_id), entries in sorted(series.items()):
            entries.sort(key=lambda r: r.collected_at)
            statuses = [r.status for r in entries]

            regressions = [
                entries[i]
                for i in range(1, len(statuses))
                if statuses[i - 1] == ComplianceStatus.PASS.value
                and statuses[i] == ComplianceStatus.FAIL.value
            ]
            if not regressions:
                continue

            first_regression = regressions[0]
            recovered = statuses[-1] == ComplianceStatus.PASS.value
            check_severity = entries[-1].severity
            severity = (
                Severity.HIGH
                if check_severity == Severity.HIGH.value and not recovered
                else Severity.MEDIUM
            )

            records = [
                build_evidence(
                    tool=self.tool_name,
                    field=f"compliance.{check_id}.regressed_at",
                    value=first_regression.collected_at.isoformat(),
                    device_id=device_id,
                    device_label=ctx.label(device_id),
                    snapshot_ts=first_regression.collected_at,
                    detail={"check_id": check_id, "severity": check_severity},
                ),
                build_evidence(
                    tool=self.tool_name,
                    field=f"compliance.{check_id}",
                    value=statuses[-1],
                    device_id=device_id,
                    device_label=ctx.label(device_id),
                    snapshot_ts=entries[-1].collected_at,
                    detail={
                        "check_id": check_id,
                        "severity": check_severity,
                        "recovered": recovered,
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
                    title=(
                        f"Was passing the {check_id.replace('_', ' ')} check "
                        "and no longer is"
                    ),
                    metrics={
                        "check_id": check_id,
                        "check_severity": check_severity,
                        "regressed_at": first_regression.collected_at.isoformat(),
                        "regressions": len(regressions),
                        "current_status": statuses[-1],
                        "recovered": recovered,
                        "snapshots": len(statuses),
                    },
                    evidence_ids=[r.evidence_id for r in records],
                )
            )
        return out
