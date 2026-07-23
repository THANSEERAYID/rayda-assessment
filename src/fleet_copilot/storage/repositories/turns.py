"""Completed-turn results, so an answer outlives the turn that produced it.

The trace says how a turn ran; this stores what it produced. Written when a turn
reaches its answer (or its approval gate, which is still a produced result), and
overwritten if the same turn is resumed and finishes — the row is keyed by
``turn_id`` so a resume updates rather than duplicates.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Row

from ..tables import turns


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TurnRepository:
    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def upsert(
        self,
        *,
        turn_id: str,
        thread_id: str,
        company_id: str,
        kind: str,
        question: str,
        result: dict,
    ) -> None:
        payload = {
            "turn_id": turn_id,
            "thread_id": thread_id,
            "company_id": company_id,
            "kind": kind,
            "question": question,
            "result": json.dumps(result, default=str),
            "created_at": _now(),
        }
        dialect = self.conn.dialect.name
        if dialect == "postgresql":
            stmt = pg_insert(turns).values(**payload)
            stmt = stmt.on_conflict_do_update(
                index_elements=[turns.c.turn_id],
                # A resume keeps the original question and time; only the produced
                # result changes.
                set_={"result": stmt.excluded.result},
            )
            self.conn.execute(stmt)
            return
        # SQLite (tests): emulate the upsert without a dialect-specific clause.
        exists = self.conn.execute(
            select(turns.c.turn_id).where(turns.c.turn_id == turn_id)
        ).first()
        if exists:
            self.conn.execute(
                turns.update()
                .where(turns.c.turn_id == turn_id)
                .values(result=payload["result"])
            )
        else:
            self.conn.execute(turns.insert().values(**payload))

    def list_for_company(
        self, company_id: str, *, kind: str | None = None, limit: int = 100
    ) -> list[Row]:
        stmt = select(turns).where(turns.c.company_id == company_id)
        if kind:
            stmt = stmt.where(turns.c.kind == kind)
        stmt = stmt.order_by(turns.c.created_at.desc()).limit(limit)
        return list(self.conn.execute(stmt).fetchall())
