"""The proposal → approval → execution state machine.

Three properties are enforced here rather than requested in a prompt:

*Nothing executes at proposal time.* An action tool call only ever writes a row
with status ``PROPOSED``. The transition table in ``ActionRepository`` has no
edge from ``PROPOSED`` to ``EXECUTED``, so even a caller that tried to skip the
gate cannot.

*Every action must be justified by resolvable evidence.* The cited ids are
checked against the turn's ledger before the proposal is written. An action the
agent cannot support is refused rather than shown to an administrator, because a
proposal that arrives with an unverifiable reason is worse than no proposal — it
invites a rubber-stamp approval.

*Every transition is audited.* Proposal, approval, rejection and execution each
append to the audit log with the deciding actor.

Execution is simulated: this system has no ticketing or procurement backend, so
``execute`` records the outcome and marks the action executed. The boundary where
a real integration would attach is marked below.
"""
from __future__ import annotations

from sqlalchemy.engine import Connection

from ..domain.action_policy import WHAT_IT_NEEDS, supports_directly
from ..domain.enums import ActionStatus, ActionType, AuditEventType
from ..domain.errors import InsufficientEvidence
from ..domain.models import ActionDecision, ProposedAction
from ..evidence.ledger import EvidenceLedger
from ..evidence.review import assess_proposal
from ..evidence.validator import validate_action_evidence
from ..storage.repositories.actions import ActionRepository, IllegalTransition
from ..domain.text import format_device
from ..storage.repositories.audit import AuditRepository
from ..storage.repositories.devices import DeviceRepository
from ..storage.repositories.emails import EmailRepository
from ..storage.repositories.tickets import TicketRepository
from .email import employee_address, send_email
from .tenant import TenantGuard

# Which target each action type requires. Enforced so a proposal cannot be
# written without something to act on.
_REQUIRED_TARGET: dict[ActionType, str] = {
    ActionType.CREATE_UPGRADE_ORDER: "device",
    ActionType.OPEN_REMEDIATION_TICKET: "device",
    ActionType.FLAG_DEVICE_FOR_REPLACEMENT: "device",
    ActionType.NOTIFY_EMPLOYEE: "employee",
}

class ActionService:
    def __init__(self, conn: Connection, company_id: str, thread_id: str) -> None:
        self.conn = conn
        self.company_id = company_id
        self.thread_id = thread_id
        self.actions = ActionRepository(conn)
        self.audit = AuditRepository(conn)
        self.guard = TenantGuard(conn, company_id, thread_id)

    # ------------------------------------------------------------------
    def propose(
        self,
        *,
        action_type: ActionType,
        justification: str,
        evidence_ids: list[str],
        ledger: EvidenceLedger,
        target_device_id: str | None = None,
        target_employee_id: str | None = None,
        params: dict | None = None,
    ) -> ProposedAction:
        """Validate and record a proposal. Never executes anything."""
        required = _REQUIRED_TARGET[action_type]
        if required == "device":
            if not target_device_id:
                raise InsufficientEvidence(
                    f"{action_type.value} requires a target device_id."
                )
            self.guard.assert_device(target_device_id)
        else:
            if not target_employee_id:
                raise InsufficientEvidence(
                    f"{action_type.value} requires a target employee_id."
                )
            self.guard.assert_employee(target_employee_id)

        if not (justification or "").strip():
            raise InsufficientEvidence(
                f"{action_type.value} requires a justification describing why it is needed."
            )

        resolved = validate_action_evidence(evidence_ids, ledger)

        # Evidence that is real but about a different device does not justify
        # acting on this one — a subtle failure the id check alone would miss.
        if target_device_id:
            cited_devices = {r.device_id for r in resolved if r.device_id}
            if cited_devices and target_device_id not in cited_devices:
                raise InsufficientEvidence(
                    f"The evidence cited for this action describes "
                    f"{', '.join(sorted(cited_devices))}, not {target_device_id}.",
                    target_device_id=target_device_id,
                )

        # ... and evidence about the right device still has to be about the
        # right *thing*. Without this, a blanket "replace everything" cites each
        # device's model name and passes.
        cited_fields = [r.field for r in resolved]
        if not supports_directly(action_type, cited_fields):
            raise InsufficientEvidence(
                f"{action_type.value} needs {WHAT_IT_NEEDS[action_type]} "
                f"The evidence cited describes "
                f"{', '.join(sorted(set(cited_fields))) or 'nothing relevant'}.",
                action_type=action_type.value,
            )

        # Assessed here because this is the only point at which the cited
        # evidence is resolvable; the ledger does not outlive the turn.
        draft = ProposedAction(
            action_id="pending",
            thread_id=self.thread_id,
            company_id=self.company_id,
            action_type=action_type,
            target_device_id=target_device_id,
            target_employee_id=target_employee_id,
            justification=justification,
            evidence_ids=[r.evidence_id for r in resolved],
        )
        review = assess_proposal(
            draft, {r.evidence_id: r.model_dump(mode="json") for r in resolved}
        )

        target_label = None
        if target_device_id:
            row = DeviceRepository(self.conn).get_device(
                self.company_id, target_device_id
            )
            if row is not None:
                target_label = format_device(row.hostname, row.model_name)

        action = self.actions.propose(
            review=review,
            target_label=target_label,
            thread_id=self.thread_id,
            company_id=self.company_id,
            action_type=action_type,
            justification=justification.strip(),
            evidence_ids=[r.evidence_id for r in resolved],
            target_device_id=target_device_id,
            target_employee_id=target_employee_id,
            params=params or {},
        )
        self.audit.record(
            event_type=AuditEventType.ACTION_PROPOSED,
            company_id=self.company_id,
            thread_id=self.thread_id,
            summary=_summarise_action("Proposed", action),
            detail={
                "action_id": action.action_id,
                "evidence_ids": action.evidence_ids,
                "params": action.params,
            },
        )
        return action

    # ------------------------------------------------------------------
    def apply_decisions(
        self, decisions: list[ActionDecision], *, decided_by: str
    ) -> list[ProposedAction]:
        """Apply a human's verdicts, then execute only what was approved.

        Matching terminal states are idempotent: approving an already-executed
        action (or rejecting an already-rejected one) returns the current row
        instead of crashing. That covers double-clicks and the Approvals-queue
        fallback when the LangGraph interrupt is gone but the row was already
        carried out. Conflicting re-decisions still raise ``IllegalTransition``.
        """
        results: list[ProposedAction] = []
        for decision in decisions:
            action = self.actions.get(self.company_id, decision.action_id)
            if action is None:
                # Either unknown or another tenant's action; both are invisible.
                continue
            if decision.approved:
                if action.status is ActionStatus.EXECUTED:
                    results.append(action)
                    continue
                if action.status is ActionStatus.APPROVED:
                    results.append(self._execute(action, decided_by=decided_by))
                    continue
                if action.status is not ActionStatus.PROPOSED:
                    raise IllegalTransition(
                        f"Cannot move action {action.action_id} from "
                        f"{action.status.value} to approved"
                    )
                approved = self.actions.transition(
                    decision.action_id, ActionStatus.APPROVED, decided_by=decided_by
                )
                self.audit.record(
                    event_type=AuditEventType.ACTION_APPROVED,
                    company_id=self.company_id,
                    thread_id=self.thread_id,
                    actor=decided_by,
                    summary=_summarise_action("Approved", approved),
                    detail={
                        "action_id": approved.action_id,
                        "note": decision.note,
                    },
                )
                results.append(self._execute(approved, decided_by=decided_by))
            else:
                if action.status is ActionStatus.REJECTED:
                    results.append(action)
                    continue
                if action.status is not ActionStatus.PROPOSED:
                    raise IllegalTransition(
                        f"Cannot move action {action.action_id} from "
                        f"{action.status.value} to rejected"
                    )
                rejected = self.actions.transition(
                    decision.action_id, ActionStatus.REJECTED, decided_by=decided_by
                )
                self.audit.record(
                    event_type=AuditEventType.ACTION_REJECTED,
                    company_id=self.company_id,
                    thread_id=self.thread_id,
                    actor=decided_by,
                    summary=_summarise_action("Rejected", rejected),
                    detail={
                        "action_id": rejected.action_id,
                        "note": decision.note,
                    },
                )
                results.append(rejected)
        return results

    # ------------------------------------------------------------------
    def _execute(self, action: ProposedAction, *, decided_by: str) -> ProposedAction:
        """Carry out an approved action.

        Reachable only from :meth:`apply_decisions` after a human approval, and
        only for an action already in ``APPROVED`` — ``ActionRepository`` rejects
        any other source state.

        This is the seam where a real ticketing, procurement or notification
        integration would be called. For a remediation ticket that integration
        is a local one: a row in the tickets table, which the Tickets page lists.
        """
        outcome = _describe_effect(action)
        executed = self.actions.transition(
            action.action_id, ActionStatus.EXECUTED, result=outcome
        )
        if action.action_type is ActionType.OPEN_REMEDIATION_TICKET:
            self._raise_ticket(action)
        elif action.action_type is ActionType.NOTIFY_EMPLOYEE:
            self._send_notification(action)
        self.audit.record(
            event_type=AuditEventType.ACTION_EXECUTED,
            company_id=self.company_id,
            thread_id=self.thread_id,
            actor=decided_by,
            # Prefer the human-readable outcome over the raw action_type + id.
            summary=outcome,
            detail={
                "action_id": action.action_id,
                "result": outcome,
                "params": action.params,
            },
        )
        return executed

    def _raise_ticket(self, action: ProposedAction) -> None:
        """Write the ticket an executed remediation action produces.

        Keyed by action id, so a re-run decision cannot create a second ticket
        for the same action. Never allowed to fail the execution itself — the
        action is already recorded as executed and audited.
        """
        params = action.params or {}
        try:
            TicketRepository(self.conn).create_for_action(
                action_id=action.action_id,
                company_id=self.company_id,
                device_id=action.target_device_id,
                device_label=action.target_label,
                check_id=params.get("check_id"),
                note=params.get("note"),
            )
        except Exception:  # pragma: no cover - a missing ticket must not undo execution
            pass

    def _send_notification(self, action: ProposedAction) -> None:
        """Send (or simulate) the email an executed notify_employee produces.

        Real delivery only happens when SMTP is configured; otherwise the send is
        simulated. Either way the outcome is recorded in the emails table, keyed
        to the action so a re-run cannot send twice. Never fails the execution.
        """
        params = action.params or {}
        employee_id = action.target_employee_id or ""
        message = params.get("message") or action.justification or ""
        to_address = employee_address(employee_id, self.company_id)
        subject = params.get("subject") or "A message about your device"
        try:
            result = send_email(to=to_address, subject=subject, text_content=message)
            EmailRepository(self.conn).record(
                company_id=self.company_id,
                to_address=to_address,
                subject=subject,
                body=message,
                status=result.status,
                error=result.error,
                employee_id=employee_id or None,
                action_id=action.action_id,
            )
        except Exception:  # pragma: no cover - a missing email log must not undo execution
            pass

    # ------------------------------------------------------------------
    def pending(self) -> list[ProposedAction]:
        return self.actions.list_by_status(self.company_id, ActionStatus.PROPOSED)

    def for_thread(self) -> list[ProposedAction]:
        return self.actions.list_for_thread(self.thread_id)


_ACTION_LABELS: dict[ActionType, str] = {
    ActionType.CREATE_UPGRADE_ORDER: "upgrade order",
    ActionType.OPEN_REMEDIATION_TICKET: "remediation ticket",
    ActionType.FLAG_DEVICE_FOR_REPLACEMENT: "replacement flag",
    ActionType.NOTIFY_EMPLOYEE: "employee notification",
}


def _summarise_action(verb: str, action: ProposedAction) -> str:
    """Audit-log line for a proposal / approval / rejection.

    Names the target device rather than the action id — readers of the audit
    trail care about what was decided for which machine, not the row key.
    """
    label = _ACTION_LABELS.get(
        action.action_type, action.action_type.value.replace("_", " ")
    )
    target = (
        action.target_label
        or action.target_device_id
        or action.target_employee_id
        or "unknown target"
    )
    return f"{verb} {label} for {target}"


def _describe_effect(action: ProposedAction) -> str:
    """The record of what an execution did, stored on the action.

    Names the device rather than its serial: this line is read months later in
    the audit trail, where "acme-thinkpad-8" is recognisable and a serial is not.
    """
    params = action.params or {}
    target = action.target_label or action.target_device_id
    if action.action_type is ActionType.CREATE_UPGRADE_ORDER:
        return (
            f"Upgrade order raised for {target}: "
            f"{params.get('component', 'component')} -> {params.get('spec', 'spec')}"
        )
    if action.action_type is ActionType.OPEN_REMEDIATION_TICKET:
        return (
            f"Remediation ticket opened for {target} "
            f"on the {str(params.get('check_id', 'unspecified')).replace('_', ' ')} check"
        )
    if action.action_type is ActionType.FLAG_DEVICE_FOR_REPLACEMENT:
        return f"{target} flagged for replacement"
    if action.action_type is ActionType.NOTIFY_EMPLOYEE:
        return f"Notification queued for {action.target_employee_id}"
    return "Action executed"
