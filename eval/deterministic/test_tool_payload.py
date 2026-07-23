"""Tool payloads may shrink, but never below what a claim needs to be cited.

The risk these tests guard is subtle: shaping the model's copy of a result is
safe, and shaping the ledger's copy would silently weaken grounding. Nothing
here calls a model.
"""
from __future__ import annotations

import json

from fleet_copilot.agent.tool_payload import digest, trim_for_model


def _payload() -> dict:
    return {
        "match_count": 2,
        "total_devices_considered": 10,
        "as_of_mode": "latest",
        "note": None,
        "matches": [
            {
                "device_id": "DEV1",
                "device_label": "acme-macbook-2 (MacBook Pro)",
                "disk_free_pct": 51.0,
                "battery_cycle_count": 532,
                "evidence_ids": ["ev-a", "ev-b"],
            },
            {
                "device_id": "DEV2",
                "device_label": "acme-dell-9 (Dell XPS 15)",
                "disk_free_pct": 12.5,
                "battery_cycle_count": 948,
                "evidence_ids": ["ev-c"],
            },
        ],
        "evidence": [
            {
                "evidence_id": "ev-a",
                "tool": "query_devices",
                "device_id": "DEV1",
                "device_label": "acme-macbook-2 (MacBook Pro)",
                "snapshot_ts": "2026-06-12 09:47:00",
                "field": "battery.cycle_count",
                "value": 532,
                "detail": {"disk_free_pct": 51.0, "os_version": "14.5"},
            },
            {
                "evidence_id": "ev-b",
                "tool": "query_devices",
                "device_id": "DEV1",
                "device_label": "acme-macbook-2 (MacBook Pro)",
                "snapshot_ts": "2026-06-12 09:47:00",
                "field": "disk.free_pct",
                "value": 51.0,
                "detail": {"os_version": "14.5"},
            },
            {
                "evidence_id": "ev-c",
                "tool": "query_devices",
                "device_id": "DEV2",
                "device_label": "acme-dell-9 (Dell XPS 15)",
                "snapshot_ts": "2026-06-12 09:47:00",
                "field": "disk.free_pct",
                "value": 12.5,
                "detail": {"os_version": "11"},
            },
        ],
    }


# -- trim_for_model ---------------------------------------------------------


def test_trim_keeps_every_evidence_id_and_value():
    """What a claim cites must survive; that is the whole constraint."""
    trimmed = trim_for_model(_payload())
    assert [r["evidence_id"] for r in trimmed["evidence"]] == ["ev-a", "ev-b", "ev-c"]
    assert [r["value"] for r in trimmed["evidence"]] == [532, 51.0, 12.5]
    assert [r["field"] for r in trimmed["evidence"]] == [
        "battery.cycle_count",
        "disk.free_pct",
        "disk.free_pct",
    ]


def test_trim_drops_detail_and_duplicated_labels():
    trimmed = trim_for_model(_payload())
    assert all("detail" not in r for r in trimmed["evidence"])
    # Every label here is also on a match row, so none needs repeating.
    assert all("device_label" not in r for r in trimmed["evidence"])
    assert all("device_id" in r for r in trimmed["evidence"])


def test_trim_keeps_a_label_no_sibling_row_carries():
    """Compliance and scan results emit evidence with no matching row."""
    payload = {
        "evidence": [
            {
                "evidence_id": "ev-x",
                "device_label": "acme-thinkpad-8 (ThinkPad X1 Carbon)",
                "field": "compliance.disk_encryption",
                "value": "fail",
                "detail": {},
            }
        ]
    }
    trimmed = trim_for_model(payload)
    assert trimmed["evidence"][0]["device_label"] == (
        "acme-thinkpad-8 (ThinkPad X1 Carbon)"
    )


def test_trim_leaves_matches_untouched():
    original = _payload()
    trimmed = trim_for_model(original)
    assert trimmed["matches"] == original["matches"]
    assert trimmed["match_count"] == 2


def test_trim_does_not_mutate_the_ledgers_copy():
    """The caller ingests the same object; shaping must not reach back into it."""
    payload = _payload()
    trim_for_model(payload)
    assert payload["evidence"][0]["detail"] == {"disk_free_pct": 51.0, "os_version": "14.5"}
    assert payload["evidence"][0]["device_label"] == "acme-macbook-2 (MacBook Pro)"


def test_trim_passes_errors_through():
    error = {"error": True, "reason": "cross_tenant", "message": "denied"}
    assert trim_for_model(error) == error


def test_trim_actually_shrinks_the_payload():
    payload = _payload()
    before = len(json.dumps(payload, default=str))
    after = len(json.dumps(trim_for_model(payload), default=str))
    assert after < before


# -- digest -----------------------------------------------------------------


def test_digest_keeps_every_citable_reading():
    condensed = digest(_payload(), tool="query_devices")
    joined = " ".join(condensed["evidence"])
    for evidence_id in ("ev-a", "ev-b", "ev-c"):
        assert evidence_id in joined
    assert "532" in joined and "51.0" in joined and "12.5" in joined
    assert "battery.cycle_count" in joined


def test_digest_keeps_absence_answerable():
    """A zero-match result is an answer, and stays one after condensing."""
    condensed = digest(
        {"match_count": 0, "note": "No devices match.", "evidence": []},
        tool="query_devices",
    )
    assert condensed["match_count"] == 0
    assert condensed["note"] == "No devices match."


def test_digest_drops_the_bulky_rows_but_records_their_count():
    condensed = digest(_payload(), tool="query_devices")
    assert "matches" not in condensed
    assert condensed["matches_count"] == 2


def test_digest_names_the_device_for_each_reading():
    condensed = digest(_payload(), tool="query_devices")
    assert "acme-macbook-2 (MacBook Pro)" in condensed["evidence"][0]


def test_digest_says_it_is_condensed():
    """Otherwise the shortened rows read as the tool having returned less."""
    assert "condensed" in digest(_payload(), tool="query_devices")


def test_digest_preserves_tool_errors():
    condensed = digest(
        {"error": True, "reason": "tool_failure", "message": "boom"}, tool="query_devices"
    )
    assert condensed["error"] is True
    assert condensed["reason"] == "tool_failure"
    assert condensed["message"] == "boom"


def test_digest_is_much_smaller_than_the_original():
    payload = _payload()
    before = len(json.dumps(payload, default=str))
    after = len(json.dumps(digest(payload, tool="query_devices"), default=str))
    assert after < before * 0.6
