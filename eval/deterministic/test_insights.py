"""Detector correctness against independently computed ground truth."""
from __future__ import annotations

import pytest

from fleet_copilot.config import settings
from fleet_copilot.domain.enums import FindingType
from fleet_copilot.services.insights.registry import available_detectors, run_scan

from ..fixtures.ground_truth import ground_truth

COMPANIES = ["acme-001", "globex-002", "initech-003"]


def _devices(output, finding_type: FindingType) -> set[str]:
    return {f.device_id for f in output.findings if f.finding_type is finding_type}


@pytest.mark.parametrize("company_id", COMPANIES)
def test_battery_eol_matches_ground_truth(conn, company_id):
    output = run_scan(conn, company_id, detectors=["battery_eol"])
    expected = ground_truth().battery_eol(
        company_id,
        settings.battery_high_cycle_count,
        settings.battery_capacity_decline_pct,
    )
    assert _devices(output, FindingType.BATTERY_EOL) == expected


def test_battery_detector_ignores_devices_with_no_battery(conn):
    """Mac minis have no battery. Absent hardware is not a failing battery."""
    truth = ground_truth()
    output = run_scan(conn, "initech-003", detectors=["battery_eol"])
    batteryless = truth.devices_without_battery("initech-003")

    assert batteryless, "fixture expectation: initech has batteryless devices"
    assert not (_devices(output, FindingType.BATTERY_EOL) & batteryless)


@pytest.mark.parametrize("company_id", COMPANIES)
def test_ram_pressure_uses_sustained_share_not_a_single_spike(conn, company_id):
    output = run_scan(conn, company_id, detectors=["ram_pressure"])
    expected = ground_truth().devices_ram_sustained(
        company_id, settings.ram_high_used_pct, settings.ram_sustained_ratio
    )
    assert _devices(output, FindingType.RAM_PRESSURE) == expected


@pytest.mark.parametrize("company_id", COMPANIES)
def test_disk_pressure_flags_every_critically_low_device(conn, company_id):
    output = run_scan(conn, company_id, detectors=["disk_pressure"])
    critical = ground_truth().devices_below_disk_free(
        company_id, settings.disk_critical_free_pct
    )
    assert critical <= _devices(output, FindingType.DISK_PRESSURE)


@pytest.mark.parametrize("company_id", COMPANIES)
def test_compliance_drift_reports_regressions_only(conn, company_id):
    output = run_scan(conn, company_id, detectors=["compliance_drift"])
    expected = ground_truth().compliance_regressions(company_id)
    reported = {
        (f.device_id, f.metrics["check_id"])
        for f in output.findings
        if f.finding_type is FindingType.COMPLIANCE_DRIFT
    }
    assert reported == expected


def test_compliance_drift_ignores_always_failing_checks(conn):
    """os_up_to_date never transitions in this data, so it is not drift."""
    output = run_scan(conn, "acme-001", detectors=["compliance_drift"])
    checks = {f.metrics["check_id"] for f in output.findings}
    assert "os_up_to_date" not in checks


@pytest.mark.parametrize("company_id", COMPANIES)
def test_unapproved_software_matches_ground_truth(conn, company_id):
    output = run_scan(conn, company_id, detectors=["unapproved_software"])
    expected = ground_truth().devices_with_software(
        company_id, set(settings.unapproved_software)
    )
    assert _devices(output, FindingType.UNAPPROVED_SOFTWARE) == expected


@pytest.mark.parametrize("company_id", COMPANIES)
def test_findings_only_ever_describe_the_scanned_tenant(conn, company_id):
    output = run_scan(conn, company_id)
    truth = ground_truth()
    assert {f.company_id for f in output.findings} <= {company_id}
    assert {f.device_id for f in output.findings} <= truth.devices(company_id)


@pytest.mark.parametrize("company_id", COMPANIES)
def test_every_finding_carries_resolvable_evidence(conn, company_id):
    output = run_scan(conn, company_id)
    available = {e.evidence_id for e in output.evidence}
    for finding in output.findings:
        assert finding.evidence_ids, f"{finding.finding_type} has no evidence"
        assert set(finding.evidence_ids) <= available


@pytest.mark.parametrize("company_id", COMPANIES)
def test_scan_is_deterministic(conn, company_id):
    """Same data in, same findings out, in the same order."""
    first = run_scan(conn, company_id)
    second = run_scan(conn, company_id)
    signature = lambda out: [  # noqa: E731
        (f.finding_type, f.device_id, f.severity, sorted(f.evidence_ids))
        for f in out.findings
    ]
    assert signature(first) == signature(second)


def test_unknown_detector_is_rejected(conn):
    with pytest.raises(ValueError, match="Unknown detector"):
        run_scan(conn, "acme-001", detectors=["not_a_detector"])


def test_registry_exposes_every_finding_type():
    assert set(available_detectors()) == {f.value for f in FindingType}
