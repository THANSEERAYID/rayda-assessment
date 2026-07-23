"""Snapshot selection — the *only* module that decides which snapshots a query sees.

Every telemetry question over this dataset is ambiguous without an explicit rule:
each device has 30 daily snapshots, so "which devices are low on disk" could mean
*right now* or *at any point in the month*. Fixing the rule in one place is what
makes the evaluation suite's ground truth decidable.

Two conventions are load-bearing:

``reference_time``
    Windows are measured back from the newest ``collected_at`` **in the data**,
    not from wall-clock now. The dataset covers 2026-05-14 to 2026-06-12; anchoring
    to the system clock would silently return zero rows once that period passes.

``AsOfMode.AT``
    Resolves to the newest snapshot at-or-before the requested instant, never the
    nearest in either direction — "as of" must not read from the future.

Portability: "latest per device" uses ``ROW_NUMBER()`` rather than Postgres-only
``DISTINCT ON`` so the same SQL runs on the SQLite database used by the
deterministic evaluation tier.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Sequence

from sqlalchemy import Select, and_, func, select
from sqlalchemy.engine import Connection, Row

from ...domain.enums import AsOfMode
from ..tables import snapshots


class SnapshotRepository:
    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    # -- anchors ---------------------------------------------------------
    def reference_time(self, company_id: str) -> datetime | None:
        """Newest ``collected_at`` for a tenant — the anchor for all windows."""
        return self.conn.execute(
            select(func.max(snapshots.c.collected_at)).where(
                snapshots.c.company_id == company_id
            )
        ).scalar()

    # -- selection -------------------------------------------------------
    def select(
        self,
        company_id: str,
        *,
        mode: AsOfMode = AsOfMode.LATEST,
        window_days: int = 30,
        at: datetime | None = None,
        device_ids: Sequence[str] | None = None,
    ) -> list[Row]:
        """Return snapshots under the requested selection mode.

        The ``company_id`` filter is applied unconditionally here, so no caller
        can accidentally issue an unscoped telemetry query.
        """
        if mode is AsOfMode.LATEST:
            return self._latest_per_device(company_id, device_ids)
        if mode is AsOfMode.WINDOW:
            return self._window(company_id, window_days, device_ids)
        if mode is AsOfMode.AT:
            if at is None:
                raise ValueError("AsOfMode.AT requires an 'at' timestamp")
            return self._at(company_id, at, device_ids)
        raise ValueError(f"Unsupported as-of mode: {mode}")

    def _base(self, company_id: str, device_ids: Sequence[str] | None) -> Select:
        stmt = select(snapshots).where(snapshots.c.company_id == company_id)
        if device_ids:
            stmt = stmt.where(snapshots.c.device_id.in_(list(device_ids)))
        return stmt

    def _latest_per_device(
        self, company_id: str, device_ids: Sequence[str] | None
    ) -> list[Row]:
        ranked = (
            self._base(company_id, device_ids)
            .add_columns(
                func.row_number()
                .over(
                    partition_by=snapshots.c.device_id,
                    order_by=snapshots.c.collected_at.desc(),
                )
                .label("rn")
            )
            .subquery()
        )
        stmt = (
            select(ranked)
            .where(ranked.c.rn == 1)
            .order_by(ranked.c.device_id)
        )
        return list(self.conn.execute(stmt).fetchall())

    def _window(
        self, company_id: str, window_days: int, device_ids: Sequence[str] | None
    ) -> list[Row]:
        anchor = self.reference_time(company_id)
        if anchor is None:
            return []
        cutoff = anchor - timedelta(days=window_days)
        stmt = (
            self._base(company_id, device_ids)
            .where(snapshots.c.collected_at >= cutoff)
            .order_by(snapshots.c.device_id, snapshots.c.collected_at)
        )
        return list(self.conn.execute(stmt).fetchall())

    def _at(
        self, company_id: str, at: datetime, device_ids: Sequence[str] | None
    ) -> list[Row]:
        ranked = (
            self._base(company_id, device_ids)
            .where(snapshots.c.collected_at <= at)
            .add_columns(
                func.row_number()
                .over(
                    partition_by=snapshots.c.device_id,
                    order_by=snapshots.c.collected_at.desc(),
                )
                .label("rn")
            )
            .subquery()
        )
        stmt = select(ranked).where(ranked.c.rn == 1).order_by(ranked.c.device_id)
        return list(self.conn.execute(stmt).fetchall())

    # -- single-device access -------------------------------------------
    def history(
        self,
        company_id: str,
        device_id: str,
        *,
        window_days: int = 30,
    ) -> list[Row]:
        """Full time series for one device, oldest first."""
        anchor = self.reference_time(company_id)
        if anchor is None:
            return []
        cutoff = anchor - timedelta(days=window_days)
        stmt = (
            select(snapshots)
            .where(
                and_(
                    snapshots.c.company_id == company_id,
                    snapshots.c.device_id == device_id,
                    snapshots.c.collected_at >= cutoff,
                )
            )
            .order_by(snapshots.c.collected_at)
        )
        return list(self.conn.execute(stmt).fetchall())

    def raw_snapshot(
        self, company_id: str, device_id: str, at: datetime | None = None
    ) -> dict[str, Any] | None:
        """The original JSON record — what a citation ultimately resolves to."""
        stmt = select(snapshots.c.raw, snapshots.c.collected_at).where(
            and_(
                snapshots.c.company_id == company_id,
                snapshots.c.device_id == device_id,
            )
        )
        if at is not None:
            stmt = stmt.where(snapshots.c.collected_at <= at)
        row = self.conn.execute(
            stmt.order_by(snapshots.c.collected_at.desc()).limit(1)
        ).first()
        if row is None:
            return None
        return json.loads(row.raw)

    def device_ids(self, company_id: str) -> list[str]:
        stmt = (
            select(snapshots.c.device_id)
            .where(snapshots.c.company_id == company_id)
            .distinct()
            .order_by(snapshots.c.device_id)
        )
        return [r[0] for r in self.conn.execute(stmt)]
