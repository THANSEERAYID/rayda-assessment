"""Detector registry and the scan entry point."""
from __future__ import annotations

from sqlalchemy.engine import Connection

from ...config import Settings
from ...domain.enums import FindingType, Severity
from .base import Detector, DetectorContext, DetectorOutput
from .battery_eol import BatteryEndOfLifeDetector
from .compliance_drift import ComplianceDriftDetector
from .disk_pressure import DiskPressureDetector
from .ram_pressure import RamPressureDetector
from .summary import summarise_finding
from .unapproved_software import UnapprovedSoftwareDetector

DETECTORS: dict[FindingType, Detector] = {
    FindingType.DISK_PRESSURE: DiskPressureDetector(),
    FindingType.RAM_PRESSURE: RamPressureDetector(),
    FindingType.BATTERY_EOL: BatteryEndOfLifeDetector(),
    FindingType.COMPLIANCE_DRIFT: ComplianceDriftDetector(),
    FindingType.UNAPPROVED_SOFTWARE: UnapprovedSoftwareDetector(),
}

_SEVERITY_ORDER = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}


def available_detectors() -> list[str]:
    return [f.value for f in DETECTORS]


def run_scan(
    conn: Connection,
    company_id: str,
    *,
    detectors: list[str] | None = None,
    window_days: int = 30,
    settings: Settings | None = None,
) -> DetectorOutput:
    """Run the requested detectors for one tenant.

    Findings come back ordered by severity then device so the same scan produces
    the same ordering every time — the UI and the evaluation suite both depend on
    that stability.
    """
    selected: list[Detector]
    if detectors:
        wanted = {d.strip().lower() for d in detectors}
        unknown = wanted - {f.value for f in DETECTORS}
        if unknown:
            raise ValueError(
                f"Unknown detector(s): {', '.join(sorted(unknown))}. "
                f"Available: {', '.join(available_detectors())}"
            )
        selected = [DETECTORS[FindingType(name)] for name in sorted(wanted)]
    else:
        selected = list(DETECTORS.values())

    ctx = DetectorContext.build(
        conn, company_id, window_days=window_days, settings=settings
    )
    output = DetectorOutput()
    for detector in selected:
        output.extend(detector.run(ctx))

    # A plain-language explanation for each finding, composed from its own
    # computed metrics — shown in the listing, the drawer, and exports.
    for finding in output.findings:
        if not finding.explanation:
            finding.explanation = summarise_finding(finding)

    output.findings.sort(
        key=lambda f: (
            _SEVERITY_ORDER.get(f.severity, 9),
            f.finding_type.value,
            f.device_id,
        )
    )
    return output
