"""A log of emails the system sent or simulated.

Append-only from the app's point of view: an email is a record of something that
happened at a moment, so it is written once and read thereafter. When it comes
from an executed action it is keyed to that action; a re-executed decision does
not write a second copy.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.engine import Connection, Row

from ..tables import emails


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class EmailRepository:
    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def record(
        self,
        *,
        company_id: str,
        to_address: str,
        subject: str,
        body: str,
        status: str,
        error: str | None = None,
        employee_id: str | None = None,
        action_id: str | None = None,
    ) -> str | None:
        """Write an email record. For an action-triggered email, a no-op if one
        already exists for that action (returns None then)."""
        if action_id:
            existing = self.conn.execute(
                select(emails.c.email_id).where(emails.c.action_id == action_id)
            ).first()
            if existing:
                return None
        email_id = f"eml-{uuid.uuid4().hex[:12]}"
        self.conn.execute(
            emails.insert().values(
                email_id=email_id,
                company_id=company_id,
                action_id=action_id,
                employee_id=employee_id,
                to_address=to_address,
                subject=subject,
                body=body,
                status=status,
                error=error,
                created_at=_now(),
            )
        )
        return email_id

    def list_for_company(self, company_id: str, *, limit: int = 200) -> list[Row]:
        stmt = (
            select(emails)
            .where(emails.c.company_id == company_id)
            .order_by(emails.c.created_at.desc())
            .limit(limit)
        )
        return list(self.conn.execute(stmt).fetchall())
