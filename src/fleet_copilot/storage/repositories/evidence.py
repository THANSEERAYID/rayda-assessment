"""Durable storage for citable readings.

The in-memory :class:`~fleet_copilot.evidence.ledger.EvidenceLedger` is what the
grounding validator checks against during a turn. This is the same records kept
afterwards, so a proposal approved next week can still show the telemetry it
rested on.

Writes are insert-if-absent rather than upsert: ``evidence_id`` is derived from
the reading's content, so a row that already exists is by definition identical.
Nothing here updates or deletes — like the audit log, evidence that can be
edited is not evidence.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.engine import Connection

from ...domain.models import Evidence
from ..tables import evidence as evidence_table


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class EvidenceRepository:
    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def record_many(
        self,
        records: list[Evidence],
        *,
        company_id: str,
        thread_id: str | None = None,
        turn_id: str | None = None,
    ) -> int:
        """Persist readings that are not already stored. Returns how many were new."""
        if not records:
            return 0

        wanted = {r.evidence_id for r in records}
        existing = {
            row.evidence_id
            for row in self.conn.execute(
                select(evidence_table.c.evidence_id).where(
                    evidence_table.c.evidence_id.in_(wanted)
                )
            )
        }
        fresh = [r for r in records if r.evidence_id not in existing]
        if not fresh:
            return 0

        # A tool can emit the same reading twice in one turn; the primary key
        # would reject the batch, so collapse duplicates before inserting.
        seen: set[str] = set()
        rows = []
        for record in fresh:
            if record.evidence_id in seen:
                continue
            seen.add(record.evidence_id)
            rows.append(
                {
                    "evidence_id": record.evidence_id,
                    "company_id": company_id,
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "tool": record.tool,
                    "device_id": record.device_id,
                    "device_label": record.device_label,
                    "snapshot_ts": record.snapshot_ts,
                    "field": record.field,
                    # Values are heterogeneous — floats, strings, booleans, and
                    # occasionally a list — so they are stored as JSON to come
                    # back the same type they went in as.
                    "value": json.dumps(record.value, default=str),
                    "detail": json.dumps(record.detail or {}, default=str),
                    "created_at": _now(),
                }
            )
        self.conn.execute(evidence_table.insert(), rows)
        return len(rows)

    def get_many(self, evidence_ids: list[str], *, company_id: str) -> list[Evidence]:
        """Resolve ids to records, scoped to one tenant.

        The company filter is the point: an id is a bare string on a proposal,
        and without this a caller could read another tenant's telemetry by
        guessing or replaying one.
        """
        if not evidence_ids:
            return []
        stmt = (
            select(evidence_table)
            .where(evidence_table.c.evidence_id.in_(set(evidence_ids)))
            .where(evidence_table.c.company_id == company_id)
        )
        return [_to_model(row) for row in self.conn.execute(stmt)]


def _to_model(row) -> Evidence:
    return Evidence(
        evidence_id=row.evidence_id,
        tool=row.tool,
        device_id=row.device_id,
        device_label=row.device_label,
        snapshot_ts=row.snapshot_ts,
        field=row.field,
        value=json.loads(row.value) if row.value is not None else None,
        detail=json.loads(row.detail or "{}"),
    )
