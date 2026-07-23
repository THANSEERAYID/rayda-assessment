"""Remediation tickets — the artifact an executed ticket action produces."""
from __future__ import annotations

from fastapi import APIRouter

from ...storage.db import connect
from ...storage.repositories.tickets import TicketRepository
from ..deps import require_company

router = APIRouter(tags=["tickets"])


@router.get("/tickets")
def list_tickets(company_id: str) -> dict:
    """A tenant's remediation tickets, newest first.

    Created when an ``open_remediation_ticket`` proposal is approved and executed;
    tenant-scoped like every other read here.
    """
    require_company(company_id)
    with connect() as conn:
        rows = TicketRepository(conn).list_for_company(company_id)
    return {
        "company_id": company_id,
        "tickets": [
            {
                "ticket_id": r.ticket_id,
                "action_id": r.action_id,
                "device_id": r.device_id,
                "device_label": r.device_label,
                "check_id": r.check_id,
                "note": r.note,
                "status": r.status,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }
