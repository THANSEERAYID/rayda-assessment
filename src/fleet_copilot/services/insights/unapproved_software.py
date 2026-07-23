"""Unapproved software present on managed devices.

The watchlist lives in ``config`` rather than here so an administrator can adjust
it without a code change, and so the evaluation suite asserts against the same
list the detector uses.

Matching is exact on the reported application name. Substring matching would be
tempting but produces false positives on unrelated products that happen to share
a fragment, and a compliance finding that cannot be trusted gets ignored.
"""
from __future__ import annotations

from ...domain.enums import FindingType, Severity
from ...domain.models import Finding
from ...evidence.ledger import build_evidence
from .base import Detector, DetectorContext, DetectorOutput


class UnapprovedSoftwareDetector(Detector):
    finding_type = FindingType.UNAPPROVED_SOFTWARE

    def run(self, ctx: DetectorContext) -> DetectorOutput:
        out = DetectorOutput()
        watchlist = list(ctx.settings.unapproved_software)
        if not watchlist:
            return out

        rows = ctx.software().latest_inventory(ctx.company_id, names=watchlist)
        by_device: dict[str, list] = {}
        for row in rows:
            by_device.setdefault(row.device_id, []).append(row)

        for device_id, entries in sorted(by_device.items()):
            entries.sort(key=lambda r: r.name)
            records = [
                build_evidence(
                    tool=self.tool_name,
                    field="installed_software.name",
                    value=entry.name,
                    device_id=device_id,
                    device_label=ctx.label(device_id),
                    snapshot_ts=entry.collected_at,
                    detail={"version": entry.version, "publisher": entry.publisher},
                )
                for entry in entries
            ]
            out.evidence.extend(records)

            names = [e.name for e in entries]
            out.findings.append(
                Finding(
                    finding_type=self.finding_type,
                    device_id=device_id,
                    device_label=ctx.label(device_id),
                    company_id=ctx.company_id,
                    severity=Severity.MEDIUM,
                    title=f"Unapproved software installed: {', '.join(names)}",
                    metrics={
                        "applications": names,
                        "versions": {e.name: e.version for e in entries},
                        "observed_at": entries[-1].collected_at.isoformat(),
                    },
                    evidence_ids=[r.evidence_id for r in records],
                )
            )
        return out
