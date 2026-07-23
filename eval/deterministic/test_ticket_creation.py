"""Executing a remediation-ticket action creates exactly one ticket.

The ticket is the concrete artifact of an approved ``open_remediation_ticket`` —
it must appear only after execution (never at proposal or on rejection), only for
that action type, once per action, and never leak across tenants. No model calls.
"""
from __future__ import annotations

from fleet_copilot.domain.enums import ActionStatus, ActionType
from fleet_copilot.domain.models import ActionDecision
from fleet_copilot.evidence.ledger import EvidenceLedger, build_evidence
from fleet_copilot.services.actions import ActionService
from fleet_copilot.storage.repositories.tickets import TicketRepository

from ..fixtures.ground_truth import ground_truth


def _service(conn, company="acme-001", thread="t-tickets"):
    return ActionService(conn, company, thread)


def _tickets_for(conn, action_id: str, company="acme-001"):
    # Scoped to one action, because the shared test transaction commits and
    # tickets from other tests accumulate — a company-wide count is not stable.
    return [
        t
        for t in TicketRepository(conn).list_for_company(company)
        if t.action_id == action_id
    ]


def _ticket_ledger(device_id: str) -> tuple[EvidenceLedger, str]:
    ledger = EvidenceLedger()
    record = build_evidence(
        tool="get_compliance_status",
        field="compliance.screen_lock",
        value="fail",
        device_id=device_id,
    )
    ledger.add(record)
    return ledger, record.evidence_id


def _eol_ledger(device_id: str) -> tuple[EvidenceLedger, str]:
    ledger = EvidenceLedger()
    record = build_evidence(
        tool="run_insight_scan",
        field="battery.condition",
        value="Service Recommended",
        device_id=device_id,
    )
    ledger.add(record)
    return ledger, record.evidence_id


def _propose_ticket(conn, device: str):
    ledger, evidence_id = _ticket_ledger(device)
    return _service(conn).propose(
        action_type=ActionType.OPEN_REMEDIATION_TICKET,
        justification="Screen lock check has regressed to fail.",
        evidence_ids=[evidence_id],
        ledger=ledger,
        target_device_id=device,
        params={"check_id": "screen_lock", "note": "Re-enable screen lock."},
    )


def test_no_ticket_at_proposal_time(conn):
    device = sorted(ground_truth().devices("acme-001"))[0]
    action = _propose_ticket(conn, device)
    assert _tickets_for(conn, action.action_id) == []


def test_approval_creates_one_ticket(conn):
    device = sorted(ground_truth().devices("acme-001"))[0]
    action = _propose_ticket(conn, device)
    _service(conn).apply_decisions(
        [ActionDecision(action_id=action.action_id, approved=True)],
        decided_by="it-admin",
    )
    tickets = _tickets_for(conn, action.action_id)
    assert len(tickets) == 1
    t = tickets[0]
    assert t.device_id == device
    assert t.check_id == "screen_lock"
    assert t.status == "open"


def test_rejection_creates_no_ticket(conn):
    device = sorted(ground_truth().devices("acme-001"))[0]
    action = _propose_ticket(conn, device)
    _service(conn).apply_decisions(
        [ActionDecision(action_id=action.action_id, approved=False)],
        decided_by="it-admin",
    )
    assert _tickets_for(conn, action.action_id) == []


def test_only_ticket_actions_produce_tickets(conn):
    """A replacement flag is executed, but it is not a ticket."""
    device = sorted(ground_truth().devices("acme-001"))[1]
    ledger, evidence_id = _eol_ledger(device)
    action = _service(conn).propose(
        action_type=ActionType.FLAG_DEVICE_FOR_REPLACEMENT,
        justification="Battery is at end of life.",
        evidence_ids=[evidence_id],
        ledger=ledger,
        target_device_id=device,
    )
    result = _service(conn).apply_decisions(
        [ActionDecision(action_id=action.action_id, approved=True)],
        decided_by="it-admin",
    )
    assert result[0].status is ActionStatus.EXECUTED
    assert _tickets_for(conn, action.action_id) == []


def test_a_ticket_is_scoped_to_its_tenant(conn):
    device = sorted(ground_truth().devices("acme-001"))[0]
    action = _propose_ticket(conn, device)
    _service(conn).apply_decisions(
        [ActionDecision(action_id=action.action_id, approved=True)],
        decided_by="it-admin",
    )
    assert _tickets_for(conn, action.action_id, company="globex-002") == []


def test_recording_the_same_action_twice_makes_one_ticket(conn):
    """Idempotent on action id — a retried execution cannot duplicate."""
    repo = TicketRepository(conn)
    first = repo.create_for_action(
        action_id="act-dup", company_id="acme-001", device_id="DEV",
        device_label="dev", check_id="screen_lock", note="x",
    )
    second = repo.create_for_action(
        action_id="act-dup", company_id="acme-001", device_id="DEV",
        device_label="dev", check_id="screen_lock", note="x",
    )
    assert first is not None
    assert second is None
    assert len(_tickets_for(conn, "act-dup")) == 1
