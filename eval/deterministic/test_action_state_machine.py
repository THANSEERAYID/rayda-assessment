"""The approval gate.

The claim being tested is structural: an action cannot be executed without a
human decision. That is checked three ways — the transition table refuses the
jump, the graph has no edge that bypasses approval, and a proposal made through
the real tool boundary lands in ``proposed`` and stays there.
"""
from __future__ import annotations

import pytest

from fleet_copilot.agent.graph import build_graph
from fleet_copilot.domain.enums import ActionStatus, ActionType, AuditEventType
from fleet_copilot.domain.errors import InsufficientEvidence
from fleet_copilot.domain.models import ActionDecision
from fleet_copilot.evidence.ledger import EvidenceLedger, build_evidence
from fleet_copilot.services.actions import ActionService
from fleet_copilot.storage.repositories.actions import (
    ActionRepository,
    IllegalTransition,
)
from fleet_copilot.storage.repositories.audit import AuditRepository

from ..fixtures.ground_truth import ground_truth


def _ledger_for(device_id: str) -> tuple[EvidenceLedger, str]:
    ledger = EvidenceLedger()
    record = build_evidence(
        tool="query_devices",
        field="disk_free_pct",
        value=2.0,
        device_id=device_id,
    )
    ledger.add(record)
    return ledger, record.evidence_id


def _eol_ledger_for(device_id: str) -> tuple[EvidenceLedger, str]:
    """Battery end-of-life evidence — what a replacement legitimately rests on."""
    ledger = EvidenceLedger()
    record = build_evidence(
        tool="run_insight_scan",
        field="battery.condition",
        value="Service Recommended",
        device_id=device_id,
    )
    ledger.add(record)
    return ledger, record.evidence_id


def _service(conn, thread_id="t-actions"):
    return ActionService(conn, "acme-001", thread_id)


def test_graph_has_no_path_to_execution_that_skips_approval():
    graph = build_graph().get_graph()
    predecessors = [e.source for e in graph.edges if e.target == "execute_action"]
    assert predecessors == ["approval"]


def test_partial_approval_returns_to_the_gate_for_what_is_left():
    """Deciding a subset must not close the gate on the rest of the batch."""
    from fleet_copilot.agent.nodes.approval import route_after_execute
    from fleet_copilot.agent.state import AgentState

    still_open = AgentState(
        proposals=[{"action_id": "a"}, {"action_id": "b"}],
        executed=[{"action_id": "a", "status": "executed"}],
    )
    assert route_after_execute(still_open) == "approval"

    all_done = AgentState(
        proposals=[{"action_id": "a"}, {"action_id": "b"}],
        executed=[
            {"action_id": "a", "status": "executed"},
            {"action_id": "b", "status": "rejected"},
        ],
    )
    assert route_after_execute(all_done) == "respond"

def test_proposal_is_created_in_proposed_state(conn):
    device = sorted(ground_truth().devices("acme-001"))[0]
    ledger, evidence_id = _ledger_for(device)

    action = _service(conn).propose(
        action_type=ActionType.OPEN_REMEDIATION_TICKET,
        justification="Disk is nearly full.",
        evidence_ids=[evidence_id],
        ledger=ledger,
        target_device_id=device,
        params={"check_id": "disk_space", "note": "clear space"},
    )

    assert action.status is ActionStatus.PROPOSED
    assert action.result is None


def test_execution_cannot_be_reached_directly_from_proposed(conn):
    device = sorted(ground_truth().devices("acme-001"))[0]
    ledger, evidence_id = _eol_ledger_for(device)
    action = _service(conn).propose(
        action_type=ActionType.FLAG_DEVICE_FOR_REPLACEMENT,
        justification="Battery is at end of life.",
        evidence_ids=[evidence_id],
        ledger=ledger,
        target_device_id=device,
    )

    with pytest.raises(IllegalTransition):
        ActionRepository(conn).transition(action.action_id, ActionStatus.EXECUTED)


def test_approval_executes_and_rejection_does_not(conn):
    devices = sorted(ground_truth().devices("acme-001"))
    service = _service(conn)

    proposals = []
    for device in devices[:2]:
        ledger, evidence_id = _ledger_for(device)
        proposals.append(
            service.propose(
                action_type=ActionType.OPEN_REMEDIATION_TICKET,
                justification="Needs attention.",
                evidence_ids=[evidence_id],
                ledger=ledger,
                target_device_id=device,
                params={"check_id": "screen_lock", "note": "re-enable"},
            )
        )

    results = service.apply_decisions(
        [
            ActionDecision(action_id=proposals[0].action_id, approved=True),
            ActionDecision(action_id=proposals[1].action_id, approved=False),
        ],
        decided_by="it-admin",
    )
    by_id = {r.action_id: r for r in results}

    assert by_id[proposals[0].action_id].status is ActionStatus.EXECUTED
    assert by_id[proposals[0].action_id].result
    assert by_id[proposals[1].action_id].status is ActionStatus.REJECTED
    assert by_id[proposals[1].action_id].result is None


def test_a_decided_action_cannot_be_decided_again(conn):
    device = sorted(ground_truth().devices("acme-001"))[0]
    ledger, evidence_id = _eol_ledger_for(device)
    service = _service(conn)
    action = service.propose(
        action_type=ActionType.FLAG_DEVICE_FOR_REPLACEMENT,
        justification="End of life.",
        evidence_ids=[evidence_id],
        ledger=ledger,
        target_device_id=device,
    )
    service.apply_decisions(
        [ActionDecision(action_id=action.action_id, approved=False)],
        decided_by="it-admin",
    )

    with pytest.raises(IllegalTransition):
        ActionRepository(conn).transition(action.action_id, ActionStatus.APPROVED)


def test_action_without_evidence_is_refused(conn):
    device = sorted(ground_truth().devices("acme-001"))[0]

    with pytest.raises(InsufficientEvidence):
        _service(conn).propose(
            action_type=ActionType.FLAG_DEVICE_FOR_REPLACEMENT,
            justification="I think it is old.",
            evidence_ids=[],
            ledger=EvidenceLedger(),
            target_device_id=device,
        )


def test_action_citing_unresolvable_evidence_is_refused(conn):
    device = sorted(ground_truth().devices("acme-001"))[0]

    with pytest.raises(InsufficientEvidence):
        _service(conn).propose(
            action_type=ActionType.FLAG_DEVICE_FOR_REPLACEMENT,
            justification="Battery is failing.",
            evidence_ids=["ev-fabricated"],
            ledger=EvidenceLedger(),
            target_device_id=device,
        )


def test_evidence_about_another_device_does_not_justify_this_one(conn):
    """Real evidence for the wrong device is the subtle case worth catching."""
    devices = sorted(ground_truth().devices("acme-001"))
    ledger, evidence_id = _ledger_for(devices[0])

    with pytest.raises(InsufficientEvidence, match="not"):
        _service(conn).propose(
            action_type=ActionType.FLAG_DEVICE_FOR_REPLACEMENT,
            justification="Cites a different device's telemetry.",
            evidence_ids=[evidence_id],
            ledger=ledger,
            target_device_id=devices[1],
        )


def test_action_without_justification_is_refused(conn):
    device = sorted(ground_truth().devices("acme-001"))[0]
    ledger, evidence_id = _ledger_for(device)

    with pytest.raises(InsufficientEvidence):
        _service(conn).propose(
            action_type=ActionType.OPEN_REMEDIATION_TICKET,
            justification="   ",
            evidence_ids=[evidence_id],
            ledger=ledger,
            target_device_id=device,
            params={"check_id": "screen_lock", "note": "x"},
        )


def test_every_lifecycle_transition_is_audited(conn):
    device = sorted(ground_truth().devices("acme-001"))[0]
    ledger, evidence_id = _ledger_for(device)
    service = _service(conn, thread_id="t-audit-lifecycle")
    action = service.propose(
        action_type=ActionType.CREATE_UPGRADE_ORDER,
        justification="Sustained memory pressure.",
        evidence_ids=[evidence_id],
        ledger=ledger,
        target_device_id=device,
        params={"component": "RAM", "spec": "32GB"},
    )
    service.apply_decisions(
        [ActionDecision(action_id=action.action_id, approved=True)],
        decided_by="it-admin",
    )

    events = AuditRepository(conn).list_events(
        "acme-001", thread_id="t-audit-lifecycle"
    )
    kinds = {e.event_type for e in events}
    assert {
        AuditEventType.ACTION_PROPOSED.value,
        AuditEventType.ACTION_APPROVED.value,
        AuditEventType.ACTION_EXECUTED.value,
    } <= kinds
    assert any(e.actor == "it-admin" for e in events)


def test_decisions_for_another_tenants_action_are_ignored(conn):
    device = sorted(ground_truth().devices("acme-001"))[0]
    ledger, evidence_id = _eol_ledger_for(device)
    action = _service(conn).propose(
        action_type=ActionType.FLAG_DEVICE_FOR_REPLACEMENT,
        justification="End of life.",
        evidence_ids=[evidence_id],
        ledger=ledger,
        target_device_id=device,
    )

    other_tenant = ActionService(conn, "globex-002", "t-other")
    results = other_tenant.apply_decisions(
        [ActionDecision(action_id=action.action_id, approved=True)],
        decided_by="attacker",
    )

    assert results == []
    assert (
        ActionRepository(conn).get("acme-001", action.action_id).status
        is ActionStatus.PROPOSED
    )


def test_deciding_an_action_twice_is_a_conflict_not_a_crash(conn):
    """A double-click on Approve must not reach the API as a 500."""
    device = sorted(ground_truth().devices("acme-001"))[0]
    ledger, evidence_id = _eol_ledger_for(device)
    service = _service(conn, thread_id="t-double")
    action = service.propose(
        action_type=ActionType.FLAG_DEVICE_FOR_REPLACEMENT,
        justification="End of life.",
        evidence_ids=[evidence_id],
        ledger=ledger,
        target_device_id=device,
    )
    service.apply_decisions(
        [ActionDecision(action_id=action.action_id, approved=True)],
        decided_by="it-admin",
    )

    # The second decision must be refused by the lifecycle, and the action
    # must remain exactly as the first decision left it.
    with pytest.raises(IllegalTransition):
        ActionRepository(conn).transition(action.action_id, ActionStatus.APPROVED)
    assert (
        ActionRepository(conn).get("acme-001", action.action_id).status
        is ActionStatus.EXECUTED
    )


def _evidence(device_id: str, field: str, value=2.0) -> tuple[EvidenceLedger, str]:
    ledger = EvidenceLedger()
    record = build_evidence(
        tool="query_devices", field=field, value=value, device_id=device_id
    )
    ledger.add(record)
    return ledger, record.evidence_id


def test_replacement_needs_end_of_life_evidence_not_just_any_evidence(conn):
    """The blanket-instruction attack: real evidence that justifies nothing.

    Every device has a model name and an owner, so citing those would let
    "flag the whole fleet for replacement" through if the gate only checked
    that evidence exists and names the right device.
    """
    device = sorted(ground_truth().devices("acme-001"))[0]
    ledger, evidence_id = _evidence(device, "device_identity.model_name", "MacBook Pro")

    with pytest.raises(InsufficientEvidence, match="end of life"):
        _service(conn).propose(
            action_type=ActionType.FLAG_DEVICE_FOR_REPLACEMENT,
            justification="Replacing the whole fleet.",
            evidence_ids=[evidence_id],
            ledger=ledger,
            target_device_id=device,
        )


def test_replacement_is_allowed_on_battery_end_of_life_evidence(conn):
    device = sorted(ground_truth().devices("acme-001"))[0]
    ledger, evidence_id = _evidence(device, "battery.condition", "Service Recommended")

    action = _service(conn).propose(
        action_type=ActionType.FLAG_DEVICE_FOR_REPLACEMENT,
        justification="Battery is at end of life.",
        evidence_ids=[evidence_id],
        ledger=ledger,
        target_device_id=device,
    )
    assert action.status is ActionStatus.PROPOSED


def test_a_full_disk_does_not_justify_replacing_the_device(conn):
    """A fixable condition warrants a ticket, not new hardware."""
    device = sorted(ground_truth().devices("acme-001"))[0]
    ledger, evidence_id = _evidence(device, "disk_free_pct", 2.0)

    with pytest.raises(InsufficientEvidence, match="end of life"):
        _service(conn).propose(
            action_type=ActionType.FLAG_DEVICE_FOR_REPLACEMENT,
            justification="Disk is nearly full.",
            evidence_ids=[evidence_id],
            ledger=ledger,
            target_device_id=device,
        )
    # ... but it does justify a ticket.
    ticket = _service(conn).propose(
        action_type=ActionType.OPEN_REMEDIATION_TICKET,
        justification="Disk is nearly full.",
        evidence_ids=[evidence_id],
        ledger=ledger,
        target_device_id=device,
        params={"check_id": "disk_space", "note": "clear space"},
    )
    assert ticket.status is ActionStatus.PROPOSED


def test_an_upgrade_needs_a_resource_constraint(conn):
    device = sorted(ground_truth().devices("acme-001"))[0]
    ledger, evidence_id = _evidence(device, "employee_id", "emp-acme-1000")

    with pytest.raises(InsufficientEvidence, match="insufficient for its workload"):
        _service(conn).propose(
            action_type=ActionType.CREATE_UPGRADE_ORDER,
            justification="More RAM would be nice.",
            evidence_ids=[evidence_id],
            ledger=ledger,
            target_device_id=device,
            params={"component": "RAM", "spec": "32GB"},
        )


def test_notifying_an_employee_is_not_evidence_constrained(conn):
    """It changes nothing on the fleet, so any reading is a fair reason."""
    truth = ground_truth()
    device = sorted(truth.devices("acme-001"))[0]
    employee = sorted(truth.employees("acme-001"))[0]
    ledger, evidence_id = _evidence(device, "device_identity.model_name", "MacBook Pro")

    action = _service(conn).propose(
        action_type=ActionType.NOTIFY_EMPLOYEE,
        justification="Letting them know about their device.",
        evidence_ids=[evidence_id],
        ledger=ledger,
        target_employee_id=employee,
        params={"message": "Please free up disk space."},
    )
    assert action.status is ActionStatus.PROPOSED
