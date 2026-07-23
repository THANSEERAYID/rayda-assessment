"""Citations must outlive the turn that made them, and stay tenant-scoped.

The Approvals queue holds proposals from threads whose in-memory ledger is long
gone, so "each proposal cites the telemetry behind it" is only true if the ids
still resolve. No model calls here.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from fleet_copilot.domain.models import Evidence
from fleet_copilot.storage.repositories.evidence import EvidenceRepository


def _record(evidence_id: str, field: str = "battery.cycle_count", value=1049) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        tool="run_insight_scan",
        device_id="7VP16KHV88LM",
        device_label="acme-macbook-1 (MacBook Pro)",
        snapshot_ts=datetime(2026, 6, 12, 10, 41),
        field=field,
        value=value,
        detail={"threshold": 800},
    )


def test_records_resolve_after_the_turn(conn):
    repo = EvidenceRepository(conn)
    repo.record_many([_record("ev-store-a")], company_id="acme-001")

    found = repo.get_many(["ev-store-a"], company_id="acme-001")
    assert len(found) == 1
    assert found[0].field == "battery.cycle_count"
    assert found[0].value == 1049
    assert found[0].device_label == "acme-macbook-1 (MacBook Pro)"
    assert found[0].detail == {"threshold": 800}


def test_value_keeps_its_type(conn):
    """Values are heterogeneous; a float must not come back as a string."""
    repo = EvidenceRepository(conn)
    repo.record_many(
        [
            _record("ev-store-float", "battery.full_charge_capacity", 4166.0),
            _record("ev-store-str", "battery.condition", "Service Recommended"),
            _record("ev-store-bool", "disk.encrypted", True),
            _record("ev-store-zero", "query.match_count", 0),
        ],
        company_id="acme-001",
    )
    by_id = {
        r.evidence_id: r.value
        for r in repo.get_many(
            ["ev-store-float", "ev-store-str", "ev-store-bool", "ev-store-zero"],
            company_id="acme-001",
        )
    }
    assert by_id["ev-store-float"] == 4166.0
    assert by_id["ev-store-str"] == "Service Recommended"
    assert by_id["ev-store-bool"] is True
    # Absence evidence: a falsy value that must survive the round trip intact,
    # since it is what a "nothing matched" claim cites.
    assert by_id["ev-store-zero"] == 0


def test_recording_the_same_reading_twice_is_a_no_op(conn):
    """Ids are content-derived, so a repeat is the same row, not a duplicate."""
    repo = EvidenceRepository(conn)
    assert repo.record_many([_record("ev-store-dup")], company_id="acme-001") == 1
    assert repo.record_many([_record("ev-store-dup")], company_id="acme-001") == 0
    assert len(repo.get_many(["ev-store-dup"], company_id="acme-001")) == 1


def test_duplicates_within_one_batch_do_not_break_the_insert(conn):
    """A tool can emit the same reading twice in a single result."""
    repo = EvidenceRepository(conn)
    written = repo.record_many(
        [_record("ev-store-batch"), _record("ev-store-batch")], company_id="acme-001"
    )
    assert written == 1
    assert len(repo.get_many(["ev-store-batch"], company_id="acme-001")) == 1


def test_another_tenant_cannot_resolve_the_id(conn):
    """An id on a proposal is a bare string — it must not cross tenants."""
    repo = EvidenceRepository(conn)
    repo.record_many([_record("ev-store-acme")], company_id="acme-001")
    assert repo.get_many(["ev-store-acme"], company_id="globex-002") == []


def test_unknown_ids_are_skipped_not_faked(conn):
    repo = EvidenceRepository(conn)
    repo.record_many([_record("ev-store-real")], company_id="acme-001")
    found = repo.get_many(
        ["ev-store-real", "ev-does-not-exist"], company_id="acme-001"
    )
    assert [r.evidence_id for r in found] == ["ev-store-real"]


@pytest.mark.parametrize("ids", [[], ["ev-nope"]])
def test_empty_and_missing_lookups_return_nothing(conn, ids):
    assert EvidenceRepository(conn).get_many(ids, company_id="acme-001") == []


def test_recording_nothing_is_safe(conn):
    assert EvidenceRepository(conn).record_many([], company_id="acme-001") == 0
