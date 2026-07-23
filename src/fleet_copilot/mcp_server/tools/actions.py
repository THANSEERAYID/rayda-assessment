"""State-changing action tools.

Calling one of these does **not** perform the action. It records a proposal that
an IT administrator must approve in the UI, and every call is checked against
three conditions before anything is written:

1. the target device or employee belongs to the session's tenant;
2. the cited evidence ids exist in the ledger of what the read tools actually
   returned this session;
3. the cited evidence describes the device being acted on, not merely some real
   device.

Failing any of these is a refusal, not a warning. A proposal an administrator
cannot verify is worse than no proposal, because it invites approval on trust.
"""
from __future__ import annotations

from typing import Any

from ...config import settings
from ...domain.enums import ActionType
from ...domain.errors import FleetCopilotError
from ...services.actions import ActionService
from ..context import get_context


def _propose(
    action_type: ActionType,
    *,
    justification: str,
    evidence_ids: list[str],
    company_id: str | None,
    target_device_id: str | None = None,
    target_employee_id: str | None = None,
    params: dict | None = None,
) -> dict[str, Any]:
    ctx = get_context()
    # A blanket instruction ("flag everything") should not fill the approval
    # queue. An administrator asked to approve dozens at once will not read the
    # justifications, which defeats the point of asking.
    if ctx.proposals_made >= settings.max_proposals_per_turn:
        return {
            "error": True,
            "reason": "insufficient_evidence",
            "status": "refused",
            "message": (
                f"This turn has already proposed {ctx.proposals_made} actions, "
                "the per-turn limit. Narrow the request to the devices that most "
                "need attention rather than acting on the whole fleet at once."
            ),
        }
    with ctx.connection() as conn:
        try:
            ctx.guard(conn).assert_company(company_id)
            service = ActionService(conn, ctx.company_id, ctx.thread_id or "adhoc")
            action = service.propose(
                action_type=action_type,
                justification=justification,
                evidence_ids=evidence_ids or [],
                ledger=ctx.ledger,
                target_device_id=target_device_id,
                target_employee_id=target_employee_id,
                params=params or {},
            )
            ctx.proposals_made += 1
            return {
                "status": "proposed",
                "requires_human_approval": True,
                "action": action.model_dump(mode="json"),
                "message": (
                    f"Proposed {action_type.value} (id {action.action_id}). "
                    "It has NOT been carried out — an administrator must approve it."
                ),
            }
        except FleetCopilotError as exc:
            return {
                "error": True,
                "reason": exc.reason.value,
                "message": exc.message,
                "status": "refused",
            }


def create_upgrade_order(
    device_id: str,
    component: str,
    spec: str,
    justification: str,
    evidence_ids: list[str],
    company_id: str | None = None,
) -> dict[str, Any]:
    """Propose a hardware upgrade for a device (e.g. component='RAM', spec='32GB').

    Requires ``evidence_ids`` from a prior read tool that demonstrate the need,
    and those records must describe this device. Returns a proposal awaiting
    human approval; nothing is ordered.

    Do not set ``company_id`` — the tenant is fixed by the session.
    """
    return _propose(
        ActionType.CREATE_UPGRADE_ORDER,
        justification=justification,
        evidence_ids=evidence_ids,
        company_id=company_id,
        target_device_id=device_id,
        params={"component": component, "spec": spec},
    )


def open_remediation_ticket(
    device_id: str,
    check_id: str,
    note: str,
    justification: str,
    evidence_ids: list[str],
    company_id: str | None = None,
) -> dict[str, Any]:
    """Propose a remediation ticket for a failing compliance check on a device.

    ``check_id`` must be a check this company actually collects — verify with
    ``get_compliance_status`` first. Returns a proposal awaiting human approval;
    no ticket is opened.

    Do not set ``company_id`` — the tenant is fixed by the session.
    """
    return _propose(
        ActionType.OPEN_REMEDIATION_TICKET,
        justification=justification,
        evidence_ids=evidence_ids,
        company_id=company_id,
        target_device_id=device_id,
        params={"check_id": check_id, "note": note},
    )


def flag_device_for_replacement(
    device_id: str,
    reason: str,
    justification: str,
    evidence_ids: list[str],
    company_id: str | None = None,
) -> dict[str, Any]:
    """Propose flagging a device for replacement.

    Reserve this for devices whose evidence shows end-of-life hardware rather
    than a fixable condition — a full disk warrants a ticket, not a replacement.
    Returns a proposal awaiting human approval.

    Do not set ``company_id`` — the tenant is fixed by the session.
    """
    return _propose(
        ActionType.FLAG_DEVICE_FOR_REPLACEMENT,
        justification=justification,
        evidence_ids=evidence_ids,
        company_id=company_id,
        target_device_id=device_id,
        params={"reason": reason},
    )


def notify_employee(
    employee_id: str,
    message: str,
    justification: str,
    evidence_ids: list[str],
    company_id: str | None = None,
) -> dict[str, Any]:
    """Propose sending a message to an employee about their device.

    ``employee_id`` must belong to this company. Returns a proposal awaiting
    human approval; no message is sent.

    Do not set ``company_id`` — the tenant is fixed by the session.
    """
    return _propose(
        ActionType.NOTIFY_EMPLOYEE,
        justification=justification,
        evidence_ids=evidence_ids,
        company_id=company_id,
        target_employee_id=employee_id,
        params={"message": message},
    )


def list_pending_actions(company_id: str | None = None) -> dict[str, Any]:
    """List this company's actions still awaiting an approval decision.

    Do not set ``company_id`` — the tenant is fixed by the session.
    """
    ctx = get_context()
    with ctx.connection() as conn:
        try:
            ctx.guard(conn).assert_company(company_id)
            service = ActionService(conn, ctx.company_id, ctx.thread_id or "adhoc")
            pending = service.pending()
            return {
                "pending": [a.model_dump(mode="json") for a in pending],
                "count": len(pending),
            }
        except FleetCopilotError as exc:
            return {"error": True, "reason": exc.reason.value, "message": exc.message}
