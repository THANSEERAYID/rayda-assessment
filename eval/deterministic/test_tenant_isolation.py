"""Tenant isolation, exercised through the real MCP protocol boundary.

Each test drives a tool server bound to one company and tries to reach another.
Importing the tool functions directly would be faster but would skip the boundary
that actually enforces the binding in production.
"""
from __future__ import annotations

import pytest

from fleet_copilot.domain.enums import AuditEventType
from fleet_copilot.storage.repositories.audit import AuditRepository

from ..fixtures.ground_truth import ground_truth

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_tool_server_exposes_the_expected_catalogue(mcp_session_factory):
    async with mcp_session_factory("acme-001") as session:
        names = set(await session.tool_names())
    assert {
        "query_devices",
        "get_compliance_status",
        "get_device_history",
        "get_device_snapshot",
        "run_insight_scan",
        "create_upgrade_order",
        "open_remediation_ticket",
        "flag_device_for_replacement",
        "notify_employee",
    } <= names


async def test_query_returns_only_the_bound_tenants_devices(mcp_session_factory):
    truth = ground_truth()
    async with mcp_session_factory("acme-001") as session:
        result = await session.call("query_devices", {})
    returned = {m["device_id"] for m in result["matches"]}

    assert returned == truth.devices("acme-001")
    assert not (returned & truth.devices("globex-002"))


async def test_supplying_a_foreign_company_id_is_rejected(mcp_session_factory):
    """The tripwire parameter: setting it at all is treated as an escape attempt."""
    async with mcp_session_factory("acme-001") as session:
        result = await session.call("query_devices", {"company_id": "globex-002"})

    assert result["error"] is True
    assert result["reason"] == "cross_tenant"


async def test_supplying_the_bound_company_id_is_harmless(mcp_session_factory):
    async with mcp_session_factory("acme-001") as session:
        result = await session.call("query_devices", {"company_id": "acme-001"})
    assert not result.get("error")


@pytest.mark.parametrize(
    "tool,args",
    [
        ("get_device_history", {"metric": "disk_free_pct"}),
        ("get_device_snapshot", {}),
    ],
)
async def test_foreign_device_id_is_refused(mcp_session_factory, tool, args):
    truth = ground_truth()
    foreign = sorted(truth.devices("globex-002"))[0]
    async with mcp_session_factory("acme-001") as session:
        result = await session.call(tool, {"device_id": foreign, **args})

    assert result["error"] is True
    assert result["reason"] == "cross_tenant"


async def test_refusal_does_not_reveal_that_the_device_exists(mcp_session_factory):
    """A foreign device and an imaginary one must be indistinguishable."""
    truth = ground_truth()
    foreign = sorted(truth.devices("globex-002"))[0]

    async with mcp_session_factory("acme-001") as session:
        real = await session.call("get_device_snapshot", {"device_id": foreign})
        fake = await session.call("get_device_snapshot", {"device_id": "NO-SUCH-DEVICE"})

    assert real["message"] == fake["message"]


async def test_action_tools_reject_a_foreign_target(mcp_session_factory):
    """The gap closed since v1: action tools carry no tenant parameter at all."""
    truth = ground_truth()
    foreign = sorted(truth.devices("initech-003"))[0]

    async with mcp_session_factory("acme-001") as session:
        result = await session.call(
            "flag_device_for_replacement",
            {
                "device_id": foreign,
                "reason": "testing isolation",
                "justification": "testing isolation",
                "evidence_ids": ["ev-anything"],
            },
        )

    assert result["error"] is True
    assert result["reason"] == "cross_tenant"


async def test_notify_employee_rejects_a_foreign_employee(mcp_session_factory):
    truth = ground_truth()
    foreign = sorted(truth.employees("globex-002"))[0]

    async with mcp_session_factory("acme-001") as session:
        result = await session.call(
            "notify_employee",
            {
                "employee_id": foreign,
                "message": "hello",
                "justification": "testing isolation",
                "evidence_ids": ["ev-anything"],
            },
        )

    assert result["error"] is True
    assert result["reason"] == "cross_tenant"


async def test_cross_tenant_attempts_are_audited(mcp_session_factory, engine):
    """A blocked attempt must leave a trace; a silent block hides the probe."""
    truth = ground_truth()
    foreign = sorted(truth.devices("globex-002"))[0]

    async with mcp_session_factory("acme-001", "t-audit-probe") as session:
        await session.call("get_device_snapshot", {"device_id": foreign})

    with engine.begin() as conn:
        events = AuditRepository(conn).list_events("acme-001", thread_id="t-audit-probe")

    violations = [
        e for e in events if e.event_type == AuditEventType.TENANT_VIOLATION.value
    ]
    assert violations, "cross-tenant attempt was not audited"
    assert foreign in violations[0].summary


async def test_insight_scan_is_scoped_to_the_bound_tenant(mcp_session_factory):
    truth = ground_truth()
    async with mcp_session_factory("globex-002") as session:
        result = await session.call("run_insight_scan", {})

    devices = {f["device_id"] for f in result["findings"]}
    assert devices <= truth.devices("globex-002")


async def test_a_single_turn_cannot_flood_the_approval_queue(mcp_session_factory):
    """A blanket instruction must not fill the queue with unreviewable actions.

    The cap is per *turn*, and the tool server process lives for exactly one
    turn — so it is enforced here rather than counted per thread, which would
    wrongly accumulate across a long conversation.
    """
    from fleet_copilot.config import settings

    truth = ground_truth()
    devices = sorted(truth.devices("acme-001"))

    async with mcp_session_factory("acme-001", "t-flood") as session:
        # Gather real, action-appropriate evidence for each device first.
        query = await session.call("query_devices", {"disk_free_pct_below": 100})
        by_device = {
            m["device_id"]: [
                e for e in query["evidence"]
                if e["device_id"] == m["device_id"] and "disk_free_pct" in e["field"]
            ]
            for m in query["matches"]
        }

        accepted, refused = 0, 0
        for device in devices:
            records = by_device.get(device) or []
            if not records:
                continue
            result = await session.call(
                "open_remediation_ticket",
                {
                    "device_id": device,
                    "check_id": "disk_space",
                    "note": "clear space",
                    "justification": "Storage is low.",
                    "evidence_ids": [records[0]["evidence_id"]],
                },
            )
            if result.get("error"):
                refused += 1
                assert "per-turn limit" in result["message"]
            else:
                accepted += 1

    assert accepted == settings.max_proposals_per_turn
    assert refused > 0, "the cap never engaged"
