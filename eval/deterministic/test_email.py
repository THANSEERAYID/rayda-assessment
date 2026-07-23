"""Notifying an employee sends (or simulates) an email, and it is recorded.

The dataset has no real addresses and SMTP is not configured in tests, so every
send is simulated — recorded as such, never transmitted. What matters: an email
appears only after a notify action executes, is keyed to that action (no
duplicate on re-run), carries the message, and is tenant-scoped.
"""
from __future__ import annotations

from fleet_copilot.domain.enums import ActionType
from fleet_copilot.domain.models import ActionDecision
from fleet_copilot.evidence.ledger import EvidenceLedger, build_evidence
from fleet_copilot.services.actions import ActionService
from fleet_copilot.services.email import email_enabled, employee_address, send_email
from fleet_copilot.storage.repositories.emails import EmailRepository

from ..fixtures.ground_truth import ground_truth


def _employee(company="acme-001") -> str:
    # An employee id in the shape the dataset uses.
    return f"emp-{company.split('-')[0]}-1001"


def _propose_notify(conn, employee_id: str, message="Please close heavy apps."):
    ledger = EvidenceLedger()
    device = sorted(ground_truth().devices("acme-001"))[0]
    record = build_evidence(
        tool="query_devices", field="employee_id", value=employee_id, device_id=device
    )
    ledger.add(record)
    return ActionService(conn, "acme-001", "t-email").propose(
        action_type=ActionType.NOTIFY_EMPLOYEE,
        justification="Sustained high memory.",
        evidence_ids=[record.evidence_id],
        ledger=ledger,
        target_employee_id=employee_id,
        params={"message": message, "subject": "Action on your device"},
    )


def _emails_for(conn, action_id: str, company="acme-001"):
    return [
        e
        for e in EmailRepository(conn).list_for_company(company)
        if e.action_id == action_id
    ]


# -- the service ------------------------------------------------------------


def test_unconfigured_send_is_simulated_not_transmitted():
    assert email_enabled() is False
    result = send_email(to="x@y.example", subject="s", text_content="b")
    assert result.status == "simulated"
    assert result.error is None


def test_employee_address_uses_a_non_routable_domain():
    addr = employee_address("emp-acme-1001", "acme-001")
    # RFC 2606 .example — guaranteed never to deliver to a real inbox.
    assert addr.endswith(".example")
    assert "emp-acme-1001" in addr


# -- the notify → email flow ------------------------------------------------


def test_no_email_before_approval(conn):
    action = _propose_notify(conn, _employee())
    assert _emails_for(conn, action.action_id) == []


def test_approval_records_one_email(conn):
    employee = _employee()
    action = _propose_notify(conn, employee, message="Close memory-heavy apps.")
    ActionService(conn, "acme-001", "t-email").apply_decisions(
        [ActionDecision(action_id=action.action_id, approved=True)],
        decided_by="it-admin",
    )
    sent = _emails_for(conn, action.action_id)
    assert len(sent) == 1
    e = sent[0]
    assert e.status == "simulated"
    assert e.employee_id == employee
    assert "Close memory-heavy apps." in e.body
    assert e.to_address.endswith(".example")


def test_rejection_records_no_email(conn):
    action = _propose_notify(conn, _employee())
    ActionService(conn, "acme-001", "t-email").apply_decisions(
        [ActionDecision(action_id=action.action_id, approved=False)],
        decided_by="it-admin",
    )
    assert _emails_for(conn, action.action_id) == []


def test_email_is_not_duplicated_for_an_action(conn):
    repo = EmailRepository(conn)
    first = repo.record(
        company_id="acme-001", to_address="a@b.example", subject="s", body="b",
        status="simulated", action_id="act-once",
    )
    second = repo.record(
        company_id="acme-001", to_address="a@b.example", subject="s", body="b",
        status="simulated", action_id="act-once",
    )
    assert first is not None
    assert second is None


def test_emails_are_tenant_scoped(conn):
    action = _propose_notify(conn, _employee())
    ActionService(conn, "acme-001", "t-email").apply_decisions(
        [ActionDecision(action_id=action.action_id, approved=True)],
        decided_by="it-admin",
    )
    assert _emails_for(conn, action.action_id, company="globex-002") == []
