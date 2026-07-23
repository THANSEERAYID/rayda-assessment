"""Each finding gets a plain-language summary from its own metrics.

The summary is composed deterministically — no model — so it is reproducible and
safe to show in the listing, the drawer, and exports. These check that the
figures land in the sentence and that no type is left with an empty one.
"""
from __future__ import annotations

import pytest

from fleet_copilot.domain.enums import FindingType, Severity
from fleet_copilot.domain.models import Finding
from fleet_copilot.services.insights.registry import run_scan
from fleet_copilot.services.insights.summary import summarise_finding


def _finding(finding_type: FindingType, metrics: dict, title: str = "T") -> Finding:
    return Finding(
        finding_type=finding_type,
        device_id="DEV",
        device_label="acme-macbook-1 (MacBook Pro)",
        company_id="acme-001",
        severity=Severity.HIGH,
        title=title,
        metrics=metrics,
    )


def test_battery_summary_carries_the_numbers():
    text = summarise_finding(
        _finding(
            FindingType.BATTERY_EOL,
            {"cycle_count": 1049, "condition": "Service Recommended",
             "capacity_decline_pct": 4.19, "readings": 30},
        )
    )
    assert "1049" in text
    assert "Service Recommended" in text
    assert "4.2%" in text


def test_disk_summary_states_free_space_and_projection():
    text = summarise_finding(
        _finding(
            FindingType.DISK_PRESSURE,
            {"current_free_pct": 2.59, "first_free_pct": 17.27, "days_to_full": 5.1},
        )
    )
    assert "2.6% free" in text
    assert "down from 17.3%" in text
    assert "5.1 days" in text


def test_ram_summary_reads_cleanly():
    text = summarise_finding(
        _finding(
            FindingType.RAM_PRESSURE,
            {"mean_used_pct": 94.19, "peak_used_pct": 99.0,
             "breach_snapshots": 30, "total_snapshots": 30, "threshold_pct": 85.0},
        )
    )
    assert "94.2%" in text
    assert "30 of 30" in text
    # The wording bug that produced "over above" must stay fixed.
    assert "over above" not in text


def test_compliance_summary_names_the_check():
    text = summarise_finding(
        _finding(
            FindingType.COMPLIANCE_DRIFT,
            {"check_id": "screen_lock", "current_status": "fail", "recovered": False},
        )
    )
    assert "screen lock" in text
    assert "fail" in text


def test_software_summary_lists_applications():
    text = summarise_finding(
        _finding(
            FindingType.UNAPPROVED_SOFTWARE,
            {"applications": ["uTorrent", "BitTorrent"], "versions": {}},
        )
    )
    assert "uTorrent" in text
    assert "2 unapproved" in text


def test_every_scanned_finding_gets_a_nonempty_summary(conn):
    out = run_scan(conn, "acme-001")
    assert out.findings, "expected findings to summarise"
    for finding in out.findings:
        assert finding.explanation and finding.explanation.strip()
