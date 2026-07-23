"""As-of semantics and repository correctness.

These assertions matter more than they look: if "latest" is wrong, every
point-in-time answer the agent gives is wrong in a way no amount of good
prompting can fix.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from fleet_copilot.domain.enums import AsOfMode
from fleet_copilot.storage.repositories.compliance import ComplianceRepository
from fleet_copilot.storage.repositories.snapshots import SnapshotRepository

from ..fixtures.ground_truth import ground_truth

COMPANIES = ["acme-001", "globex-002", "initech-003"]


@pytest.mark.parametrize("company_id", COMPANIES)
def test_latest_returns_exactly_one_snapshot_per_device(conn, company_id):
    rows = SnapshotRepository(conn).select(company_id, mode=AsOfMode.LATEST)
    truth = ground_truth()

    assert len(rows) == len(truth.devices(company_id))
    assert len({r.device_id for r in rows}) == len(rows)
    assert {r.device_id for r in rows} == truth.devices(company_id)


@pytest.mark.parametrize("company_id", COMPANIES)
def test_latest_picks_the_newest_snapshot(conn, company_id):
    rows = SnapshotRepository(conn).select(company_id, mode=AsOfMode.LATEST)
    truth = ground_truth()
    expected = {
        device_id: record["collected_at"]
        for device_id, record in truth.latest(company_id).items()
    }
    for row in rows:
        assert row.collected_at.isoformat() + "Z" == expected[row.device_id]


def test_window_is_anchored_to_the_data_not_wall_clock(conn):
    """A window measured from "now" would return nothing — the data is historic."""
    repo = SnapshotRepository(conn)
    rows = repo.select("acme-001", mode=AsOfMode.WINDOW, window_days=30)
    assert rows, "30-day window must not be empty for a 30-day dataset"

    anchor = repo.reference_time("acme-001")
    assert anchor is not None
    assert max(r.collected_at for r in rows) == anchor


def test_window_respects_its_cutoff(conn):
    repo = SnapshotRepository(conn)
    anchor = repo.reference_time("acme-001")
    rows = repo.select("acme-001", mode=AsOfMode.WINDOW, window_days=7)
    assert rows
    assert min(r.collected_at for r in rows) >= anchor - timedelta(days=7)


def test_at_mode_never_reads_from_the_future(conn):
    repo = SnapshotRepository(conn)
    anchor = repo.reference_time("acme-001")
    cutoff = anchor - timedelta(days=10)
    rows = repo.select("acme-001", mode=AsOfMode.AT, at=cutoff)

    assert rows
    assert all(r.collected_at <= cutoff for r in rows)
    assert len({r.device_id for r in rows}) == len(rows)


def test_at_mode_requires_a_timestamp(conn):
    with pytest.raises(ValueError):
        SnapshotRepository(conn).select("acme-001", mode=AsOfMode.AT)


@pytest.mark.parametrize("company_id", COMPANIES)
def test_repository_never_leaks_another_tenant(conn, company_id):
    rows = SnapshotRepository(conn).select(company_id, mode=AsOfMode.WINDOW)
    assert {r.company_id for r in rows} == {company_id}


def test_device_ids_do_not_collide_across_tenants():
    """The isolation tests would be vacuous if ids were unique per company anyway."""
    truth = ground_truth()
    seen: dict[str, str] = {}
    for company_id in truth.companies:
        for device_id in truth.devices(company_id):
            assert seen.setdefault(device_id, company_id) == company_id


def test_compliance_latest_is_ranked_before_status_filtering(conn):
    """A device that recovered must not still be reported as failing."""
    repo = ComplianceRepository(conn)
    failing = repo.latest_results("acme-001", status="fail")
    truth = ground_truth()

    reported = {(r.device_id, r.check_id) for r in failing}
    expected = set()
    for device_id, record in truth.latest("acme-001").items():
        for check in record["compliance_results"]:
            if check["status"] == "fail":
                expected.add((device_id, check["check_id"]))
    assert reported == expected


def test_raw_snapshot_round_trips_the_original_record(conn):
    truth = ground_truth()
    device_id = sorted(truth.devices("acme-001"))[0]
    raw = SnapshotRepository(conn).raw_snapshot("acme-001", device_id)

    assert raw is not None
    assert raw == truth.by_device[device_id][-1]


def test_raw_snapshot_is_tenant_scoped(conn):
    truth = ground_truth()
    foreign = sorted(truth.devices("globex-002"))[0]
    assert SnapshotRepository(conn).raw_snapshot("acme-001", foreign) is None
