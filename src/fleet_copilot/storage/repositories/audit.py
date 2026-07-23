"""Append-only audit log and per-node run trace.

No method here issues UPDATE or DELETE. Both tables are written once and read
thereafter — an audit trail that can be edited is not an audit trail.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import and_, func, select
from sqlalchemy.engine import Connection, Row

from ...domain.enums import AuditEventType
from ..tables import audit_log, run_steps, threads


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AuditRepository:
    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def record(
        self,
        *,
        event_type: AuditEventType,
        summary: str,
        company_id: str | None = None,
        thread_id: str | None = None,
        actor: str = "agent",
        detail: dict | None = None,
    ) -> None:
        self.conn.execute(
            audit_log.insert().values(
                thread_id=thread_id,
                company_id=company_id,
                event_type=event_type.value,
                actor=actor,
                summary=summary,
                detail=json.dumps(detail or {}, default=str),
                created_at=_now(),
            )
        )

    def list_events(
        self,
        company_id: str,
        *,
        thread_id: str | None = None,
        limit: int = 200,
    ) -> list[Row]:
        stmt = select(audit_log).where(audit_log.c.company_id == company_id)
        if thread_id:
            stmt = stmt.where(audit_log.c.thread_id == thread_id)
        return list(
            self.conn.execute(
                stmt.order_by(audit_log.c.created_at.desc(), audit_log.c.id.desc())
                .limit(limit)
            ).fetchall()
        )


class RunTraceRepository:
    """Per-node execution records backing the trace viewer."""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def record_step(
        self,
        *,
        thread_id: str,
        turn_id: str,
        seq: int,
        node: str,
        status: str,
        detail: dict | None = None,
        duration_ms: int | None = None,
    ) -> None:
        self.conn.execute(
            run_steps.insert().values(
                thread_id=thread_id,
                turn_id=turn_id,
                seq=seq,
                node=node,
                status=status,
                detail=json.dumps(detail or {}, default=str),
                duration_ms=duration_ms,
                created_at=_now(),
            )
        )

    def has_step(self, *, thread_id: str, turn_id: str, node: str, seq: int) -> bool:
        """Whether this exact step was already recorded.

        A node that suspends on ``interrupt()`` is re-executed from the top when
        the graph resumes, so everything above that call runs a second time.
        Since the replay starts from the same checkpointed state, it recomputes
        the same ``seq`` — which makes (turn, node, seq) an exact signature for
        "this is the replay, not a new step".
        """
        stmt = (
            select(run_steps.c.id)
            .where(run_steps.c.thread_id == thread_id)
            .where(run_steps.c.turn_id == turn_id)
            .where(run_steps.c.node == node)
            .where(run_steps.c.seq == seq)
            .limit(1)
        )
        return self.conn.execute(stmt).first() is not None

    def list_steps_for_company(
        self, company_id: str, *, limit_runs: int = 50
    ) -> list[Row]:
        """Every step a tenant's agent ran, newest run first.

        Capped by *run* rather than by step, because cutting at a step boundary
        would show a turn with its ending missing and read as a crash. The
        tenant filter comes from ``threads``, which holds the binding a turn
        cannot override.
        """
        recent_turns = (
            select(run_steps.c.turn_id)
            .select_from(
                run_steps.join(threads, threads.c.thread_id == run_steps.c.thread_id)
            )
            .where(threads.c.company_id == company_id)
            .group_by(run_steps.c.turn_id)
            .order_by(func.max(run_steps.c.created_at).desc())
            .limit(limit_runs)
            .subquery()
        )
        stmt = (
            select(run_steps)
            .where(run_steps.c.turn_id.in_(select(recent_turns.c.turn_id)))
            .order_by(run_steps.c.created_at, run_steps.c.seq)
        )
        return list(self.conn.execute(stmt).fetchall())

    def list_steps(self, thread_id: str, turn_id: str | None = None) -> list[Row]:
        stmt = select(run_steps).where(run_steps.c.thread_id == thread_id)
        if turn_id:
            stmt = stmt.where(run_steps.c.turn_id == turn_id)
        return list(
            self.conn.execute(
                stmt.order_by(run_steps.c.created_at, run_steps.c.seq)
            ).fetchall()
        )


class ThreadRepository:
    """Threads carry the tenant binding that later turns cannot override."""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def create(self, thread_id: str, company_id: str, title: str | None = None) -> None:
        self.conn.execute(
            threads.insert().values(
                thread_id=thread_id,
                company_id=company_id,
                created_at=_now(),
                title=title,
            )
        )

    def get(self, thread_id: str) -> Row | None:
        return self.conn.execute(
            select(threads).where(threads.c.thread_id == thread_id)
        ).first()

    def company_for(self, thread_id: str) -> str | None:
        return self.conn.execute(
            select(threads.c.company_id).where(threads.c.thread_id == thread_id)
        ).scalar()

    def list_for_company(self, company_id: str, limit: int = 50) -> list[Row]:
        return list(
            self.conn.execute(
                select(threads)
                .where(threads.c.company_id == company_id)
                .order_by(threads.c.created_at.desc())
                .limit(limit)
            ).fetchall()
        )

    def list_with_activity(self, company_id: str, limit: int = 50) -> list[Row]:
        """Threads with enough context to choose between them.

        A bare list of ``thr-25267fcce954`` style ids is unusable in a picker,
        so each row carries how many steps it recorded and when it last ran.
        Ordered by last activity rather than creation, because the thread worth
        reopening is the one that ran most recently, not the one opened first.
        """
        activity = (
            select(
                run_steps.c.thread_id.label("thread_id"),
                func.count().label("step_count"),
                func.max(run_steps.c.created_at).label("last_activity"),
            )
            .group_by(run_steps.c.thread_id)
            .subquery()
        )
        stmt = (
            select(
                threads.c.thread_id,
                threads.c.company_id,
                threads.c.title,
                threads.c.created_at,
                func.coalesce(activity.c.step_count, 0).label("step_count"),
                activity.c.last_activity,
            )
            .select_from(
                threads.outerjoin(
                    activity, threads.c.thread_id == activity.c.thread_id
                )
            )
            .where(threads.c.company_id == company_id)
            .order_by(
                func.coalesce(activity.c.last_activity, threads.c.created_at).desc()
            )
            .limit(limit)
        )
        return list(self.conn.execute(stmt).fetchall())

    def first_questions(self, thread_ids: list[str]) -> dict[str, str]:
        """The opening question of each thread, for use as a label.

        Threads are almost never titled — the question that started one is what
        an operator actually recognises it by. It is read from the planner step,
        which records the question it classified.
        """
        if not thread_ids:
            return {}
        stmt = (
            select(run_steps.c.thread_id, run_steps.c.detail, run_steps.c.created_at)
            .where(run_steps.c.thread_id.in_(thread_ids))
            .where(run_steps.c.node == "plan")
            .order_by(run_steps.c.thread_id, run_steps.c.created_at)
        )
        questions: dict[str, str] = {}
        for row in self.conn.execute(stmt):
            if row.thread_id in questions:
                continue  # ordered by time, so the first row is the first turn
            try:
                question = json.loads(row.detail or "{}").get("question")
            except (TypeError, ValueError):
                question = None
            if question:
                questions[row.thread_id] = " ".join(str(question).split())
        return questions

    def exists_for_company(self, thread_id: str, company_id: str) -> bool:
        return (
            self.conn.execute(
                select(threads.c.thread_id).where(
                    and_(
                        threads.c.thread_id == thread_id,
                        threads.c.company_id == company_id,
                    )
                )
            ).first()
            is not None
        )
