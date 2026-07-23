"""Remediation tickets produced when a ticket action is approved and executed.

One ticket per executed ``open_remediation_ticket`` action, keyed by
``action_id`` so re-executing (or a retried decision) never creates a duplicate.
Insert-if-absent, never updated by the agent — a ticket is a record of a decision
made, and the audit trail is the history of it.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.engine import Connection, Row

from ..tables import tickets


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TicketRepository:
    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def create_for_action(
        self,
        *,
        action_id: str,
        company_id: str,
        device_id: str | None,
        device_label: str | None,
        check_id: str | None,
        note: str | None,
    ) -> str | None:
        """Create the ticket for an executed action; a no-op if one exists.

        Returns the ticket id, or ``None`` if this action already has a ticket.
        """
        existing = self.conn.execute(
            select(tickets.c.ticket_id).where(tickets.c.action_id == action_id)
        ).first()
        if existing:
            return None
        # Derived from the action id so it is stable and traceable back to it.
        ticket_id = f"tkt-{action_id.removeprefix('act-')}"
        self.conn.execute(
            tickets.insert().values(
                ticket_id=ticket_id,
                company_id=company_id,
                action_id=action_id,
                device_id=device_id,
                device_label=device_label,
                check_id=check_id,
                note=note,
                status="open",
                created_at=_now(),
            )
        )
        return ticket_id

    def list_for_company(self, company_id: str, *, limit: int = 200) -> list[Row]:
        stmt = (
            select(tickets)
            .where(tickets.c.company_id == company_id)
            .order_by(tickets.c.created_at.desc())
            .limit(limit)
        )
        return list(self.conn.execute(stmt).fetchall())
