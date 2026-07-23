"""Pending-action persistence.

This repository is deliberately narrow. It can create an action only in the
``PROPOSED`` state, and the transitions it permits are enforced here rather than
in the caller, so no code path can fabricate an ``EXECUTED`` action directly.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, select, update
from sqlalchemy.engine import Connection, Row

from ...domain.enums import ActionStatus, ActionType
from ...domain.models import ProposedAction, ReviewSignal
from ..tables import pending_actions

# The only transitions the system allows.
_ALLOWED_TRANSITIONS: dict[ActionStatus, set[ActionStatus]] = {
    ActionStatus.PROPOSED: {ActionStatus.APPROVED, ActionStatus.REJECTED},
    ActionStatus.APPROVED: {ActionStatus.EXECUTED, ActionStatus.FAILED},
    ActionStatus.REJECTED: set(),
    ActionStatus.EXECUTED: set(),
    ActionStatus.FAILED: set(),
}


class IllegalTransition(RuntimeError):
    """Raised when a caller attempts a status change the lifecycle forbids."""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_model(row: Row) -> ProposedAction:
    return ProposedAction(
        action_id=row.action_id,
        thread_id=row.thread_id,
        company_id=row.company_id,
        action_type=ActionType(row.action_type),
        target_device_id=row.target_device_id,
        target_label=getattr(row, "target_label", None),
        target_employee_id=row.target_employee_id,
        params=json.loads(row.params or "{}"),
        justification=row.justification,
        evidence_ids=json.loads(row.evidence_ids or "[]"),
        review=(
            ReviewSignal.model_validate(json.loads(row.review))
            if getattr(row, "review", None)
            else None
        ),
        status=ActionStatus(row.status),
        created_at=row.created_at,
        decided_at=row.decided_at,
        decided_by=row.decided_by,
        result=row.result,
    )


class ActionRepository:
    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def propose(
        self,
        *,
        thread_id: str,
        company_id: str,
        action_type: ActionType,
        justification: str,
        evidence_ids: list[str],
        target_device_id: str | None = None,
        target_label: str | None = None,
        target_employee_id: str | None = None,
        params: dict | None = None,
        review: ReviewSignal | None = None,
    ) -> ProposedAction:
        """Record a proposal. Always lands in ``PROPOSED`` — never further."""
        action_id = f"act-{uuid.uuid4().hex[:12]}"
        row = {
            "action_id": action_id,
            "thread_id": thread_id,
            "company_id": company_id,
            "action_type": action_type.value,
            "target_device_id": target_device_id,
            "target_label": target_label,
            "target_employee_id": target_employee_id,
            "params": json.dumps(params or {}),
            "justification": justification,
            "evidence_ids": json.dumps(evidence_ids),
            "review": review.model_dump_json() if review else None,
            "status": ActionStatus.PROPOSED.value,
            "created_at": _now(),
        }
        self.conn.execute(pending_actions.insert().values(**row))
        return _to_model(self.get_row(action_id))  # type: ignore[arg-type]

    def get_row(self, action_id: str) -> Row | None:
        return self.conn.execute(
            select(pending_actions).where(pending_actions.c.action_id == action_id)
        ).first()

    def get(self, company_id: str, action_id: str) -> ProposedAction | None:
        """Tenant-scoped fetch — another tenant's action id resolves to nothing."""
        row = self.conn.execute(
            select(pending_actions).where(
                and_(
                    pending_actions.c.action_id == action_id,
                    pending_actions.c.company_id == company_id,
                )
            )
        ).first()
        return _to_model(row) if row else None

    def list_by_status(
        self, company_id: str, status: ActionStatus | None = None
    ) -> list[ProposedAction]:
        stmt = select(pending_actions).where(
            pending_actions.c.company_id == company_id
        )
        if status:
            stmt = stmt.where(pending_actions.c.status == status.value)
        rows = self.conn.execute(
            stmt.order_by(pending_actions.c.created_at.desc())
        ).fetchall()
        return [_to_model(r) for r in rows]

    def list_for_thread(self, thread_id: str) -> list[ProposedAction]:
        rows = self.conn.execute(
            select(pending_actions)
            .where(pending_actions.c.thread_id == thread_id)
            .order_by(pending_actions.c.created_at)
        ).fetchall()
        return [_to_model(r) for r in rows]

    def transition(
        self,
        action_id: str,
        new_status: ActionStatus,
        *,
        decided_by: str | None = None,
        result: str | None = None,
    ) -> ProposedAction:
        """Move an action to ``new_status``, refusing illegal jumps.

        This is the mechanism that makes "execute without approval" impossible:
        ``PROPOSED -> EXECUTED`` is simply not in the transition table.
        """
        row = self.get_row(action_id)
        if row is None:
            raise IllegalTransition(f"Unknown action {action_id}")
        current = ActionStatus(row.status)
        if new_status not in _ALLOWED_TRANSITIONS[current]:
            raise IllegalTransition(
                f"Cannot move action {action_id} from {current.value} to {new_status.value}"
            )
        values: dict = {"status": new_status.value}
        if new_status in (ActionStatus.APPROVED, ActionStatus.REJECTED):
            values["decided_at"] = _now()
            values["decided_by"] = decided_by or "unknown"
        if result is not None:
            values["result"] = result
        self.conn.execute(
            update(pending_actions)
            .where(pending_actions.c.action_id == action_id)
            .values(**values)
        )
        return _to_model(self.get_row(action_id))  # type: ignore[arg-type]
