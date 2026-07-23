"""Tool behaviour over the MCP protocol, with no model involved.

These are the questions from the brief, asked directly of the tools. If the tools
answer them correctly, any wrong answer from the agent is a reasoning failure
rather than a retrieval one — which is exactly the separation the evaluation
tiers are meant to give.
"""
from __future__ import annotations

import pytest

from fleet_copilot.config import settings

from ..fixtures.ground_truth import ground_truth

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_low_disk_query_matches_ground_truth(mcp_session_factory):
    """Brief example: "Which devices at Acme are low on disk space?"."""
    threshold = settings.disk_low_free_pct
    async with mcp_session_factory("acme-001") as session:
        result = await session.call(
            "query_devices", {"disk_free_pct_below": threshold}
        )

    returned = {m["device_id"] for m in result["matches"]}
    assert returned == ground_truth().devices_below_disk_free("acme-001", threshold)


async def test_no_high_severity_compliance_failures_anywhere(mcp_session_factory):
    """Brief example: "laptops failing high-severity compliance checks".

    The honest answer for this dataset is "none" — disk_encryption is the only
    high-severity check and it passes on all 750 snapshots. The tool must return
    an empty result *and* say why it is empty, so the agent can report the
    absence with confidence instead of treating it as a failed lookup.
    """
    for company_id in ("acme-001", "globex-002", "initech-003"):
        async with mcp_session_factory(company_id) as session:
            result = await session.call(
                "get_compliance_status", {"severity": "high", "status": "fail"}
            )
        assert result["match_count"] == 0
        assert result["note"] and "disk_encryption" in result["note"]
        assert not ground_truth().compliance_failures(company_id, severity="high")


async def test_os_version_query_is_platform_scoped(mcp_session_factory):
    """Brief example: "devices running an OS older than macOS 15"."""
    async with mcp_session_factory("acme-001") as session:
        result = await session.call(
            "query_devices", {"platform": "macOS", "os_older_than": "15"}
        )

    returned = {m["device_id"] for m in result["matches"]}
    assert returned == ground_truth().devices_on_platform_older_than(
        "acme-001", "darwin", 15
    )
    assert all(m["platform"] == "darwin" for m in result["matches"])


async def test_os_comparison_without_platform_is_refused(mcp_session_factory):
    """Ordering across platforms is undefined, so the tool must not guess."""
    async with mcp_session_factory("acme-001") as session:
        result = await session.call("query_devices", {"os_older_than": "15"})

    assert result["error"] is True
    assert "platform" in result["message"]


async def test_windows_release_tags_order_correctly(mcp_session_factory):
    """"10 22H2" < "11 22H2" < "11 23H2" — not a dotted version."""
    async with mcp_session_factory("acme-001") as session:
        older = await session.call(
            "query_devices", {"platform": "Windows", "os_older_than": "11"}
        )
    assert all(m["os_version"].startswith("10") for m in older["matches"])


async def test_empty_result_is_reported_as_an_answer(mcp_session_factory):
    async with mcp_session_factory("acme-001") as session:
        result = await session.call("query_devices", {"disk_free_pct_below": 0.001})

    assert result["match_count"] == 0
    assert "complete result" in (result["note"] or "")


async def test_history_reports_trend_statistics(mcp_session_factory):
    """The agent must not have to compute slopes itself."""
    truth = ground_truth()
    declining = sorted(truth.devices_below_disk_free("acme-001", 5.0))[0]

    async with mcp_session_factory("acme-001") as session:
        result = await session.call(
            "get_device_history",
            {"device_id": declining, "metric": "disk_free_pct"},
        )

    summary = result["summary"]
    assert summary["points"] == 30
    assert summary["slope_per_day"] < 0
    assert summary["last"] < summary["first"]


async def test_history_explains_a_metric_the_hardware_cannot_report(
    mcp_session_factory,
):
    truth = ground_truth()
    batteryless = sorted(truth.devices_without_battery("initech-003"))[0]

    async with mcp_session_factory("initech-003") as session:
        result = await session.call(
            "get_device_history",
            {"device_id": batteryless, "metric": "battery_percentage"},
        )

    assert result["points"] == []
    assert "does not report it" in (result["note"] or "")


async def test_unknown_metric_lists_the_valid_ones(mcp_session_factory):
    truth = ground_truth()
    device = sorted(truth.devices("acme-001"))[0]

    async with mcp_session_factory("acme-001") as session:
        result = await session.call(
            "get_device_history", {"device_id": device, "metric": "cpu_temperature"}
        )

    assert result["error"] is True
    assert "disk_free_pct" in result["message"]


async def test_snapshot_returns_the_raw_record(mcp_session_factory):
    truth = ground_truth()
    device = sorted(truth.devices("acme-001"))[0]

    async with mcp_session_factory("acme-001") as session:
        result = await session.call("get_device_snapshot", {"device_id": device})

    assert result["snapshot"] == truth.by_device[device][-1]


async def test_snapshot_at_a_time_never_reads_forward(mcp_session_factory):
    truth = ground_truth()
    device = sorted(truth.devices("acme-001"))[0]
    series = truth.by_device[device]
    target = series[10]["collected_at"]

    async with mcp_session_factory("acme-001") as session:
        result = await session.call(
            "get_device_snapshot", {"device_id": device, "at": target}
        )

    assert result["snapshot"]["collected_at"] <= target


async def test_every_read_result_carries_citable_evidence(mcp_session_factory):
    async with mcp_session_factory("acme-001") as session:
        result = await session.call("query_devices", {"disk_free_pct_below": 15})

    ids = {e["evidence_id"] for e in result["evidence"]}
    assert ids
    for match in result["matches"]:
        assert match["evidence_ids"]
        assert set(match["evidence_ids"]) <= ids


async def test_evidence_ids_are_stable_across_calls(mcp_session_factory):
    """Content-derived ids let evaluation cases assert on exact citations."""
    async with mcp_session_factory("acme-001") as session:
        first = await session.call("query_devices", {"disk_free_pct_below": 15})
    async with mcp_session_factory("acme-001") as session:
        second = await session.call("query_devices", {"disk_free_pct_below": 15})

    assert [e["evidence_id"] for e in first["evidence"]] == [
        e["evidence_id"] for e in second["evidence"]
    ]


async def test_fleet_summary_orients_without_leaking(mcp_session_factory):
    truth = ground_truth()
    async with mcp_session_factory("globex-002") as session:
        result = await session.call("list_fleet_summary", {})

    assert result["company_id"] == "globex-002"
    assert result["device_count"] == len(truth.devices("globex-002"))
    assert result["employee_count"] == len(truth.employees("globex-002"))


async def test_an_empty_result_still_carries_citable_evidence(mcp_session_factory):
    """Absence is a finding, and every claim must cite something.

    Without a record for the empty case the agent has nothing to point at when
    it says "nothing is failing", so the turn refuses for lack of evidence —
    which is precisely wrong for the brief's high-severity question.
    """
    async with mcp_session_factory("acme-001") as session:
        result = await session.call(
            "get_compliance_status", {"severity": "high", "status": "fail"}
        )

    assert result["match_count"] == 0
    assert result["evidence"], "an empty result must still be citable"
    record = result["evidence"][0]
    assert record["field"] == "query.match_count"
    assert record["value"] == 0


async def test_absence_records_for_different_queries_do_not_collide(
    mcp_session_factory,
):
    """They share a tool, no device and no timestamp — only the query differs."""
    async with mcp_session_factory("acme-001") as session:
        high = await session.call(
            "get_compliance_status", {"severity": "high", "status": "fail"}
        )
        impossible = await session.call("query_devices", {"disk_free_pct_below": 0.001})

    assert high["evidence"][0]["evidence_id"] != impossible["evidence"][0]["evidence_id"]


async def test_an_empty_device_query_is_citable_too(mcp_session_factory):
    async with mcp_session_factory("acme-001") as session:
        result = await session.call("query_devices", {"disk_free_pct_below": 0.001})

    assert result["match_count"] == 0
    assert result["evidence"][0]["field"] == "query.match_count"
    assert result["evidence"][0]["detail"]["devices_considered"] == 10


@pytest.mark.parametrize(
    "filters",
    [
        {"disk_free_pct_below": 15},
        {"ram_used_pct_above": 85},
        {"platform": "macOS"},
        {"battery_condition": "Service Recommended"},
        {"compliance_check_id": "screen_lock", "compliance_status": "fail"},
        {"compliance_severity": "medium", "compliance_status": "fail"},
        {"has_software": "uTorrent"},
        {},
    ],
    ids=[
        "disk", "ram", "platform", "battery",
        "compliance_check", "compliance_severity", "software", "no_filter",
    ],
)
async def test_every_match_is_citable_whatever_it_matched_on(
    mcp_session_factory, filters
):
    """The invariant a compliance-filtered query once broke.

    A match with no evidence is worse than no match: it is counted and shown,
    then fails grounding with nothing to point at, so the turn refuses despite
    having found the right devices.
    """
    async with mcp_session_factory("initech-003") as session:
        result = await session.call("query_devices", filters)

    ids = {e["evidence_id"] for e in result["evidence"]}
    for match in result["matches"]:
        assert match["evidence_ids"], (
            f"{match['device_id']} matched on {filters} but is not citable"
        )
        assert set(match["evidence_ids"]) <= ids
