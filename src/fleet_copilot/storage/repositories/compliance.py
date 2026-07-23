"""Compliance queries, including drift over time.

``compliance_results`` is unnested at ingest, so these are ordinary indexed SQL
queries rather than JSON scans over the raw telemetry blobs.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.engine import Connection, Row

from ..tables import compliance_results


class ComplianceRepository:
    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def reference_time(self, company_id: str):
        return self.conn.execute(
            select(func.max(compliance_results.c.collected_at)).where(
                compliance_results.c.company_id == company_id
            )
        ).scalar()

    def latest_results(
        self,
        company_id: str,
        *,
        severity: str | None = None,
        status: str | None = None,
        check_id: str | None = None,
    ) -> list[Row]:
        """Newest result per (device, check), optionally filtered.

        Ranking happens before filtering on ``status`` so a device that has since
        recovered is not reported as failing.
        """
        base = select(compliance_results).where(
            compliance_results.c.company_id == company_id
        )
        if severity:
            base = base.where(compliance_results.c.severity == severity)
        if check_id:
            base = base.where(compliance_results.c.check_id == check_id)

        ranked = base.add_columns(
            func.row_number()
            .over(
                partition_by=[
                    compliance_results.c.device_id,
                    compliance_results.c.check_id,
                ],
                order_by=compliance_results.c.collected_at.desc(),
            )
            .label("rn")
        ).subquery()

        stmt = select(ranked).where(ranked.c.rn == 1)
        if status:
            stmt = stmt.where(ranked.c.status == status)
        return list(
            self.conn.execute(
                stmt.order_by(ranked.c.device_id, ranked.c.check_id)
            ).fetchall()
        )

    def series(
        self, company_id: str, *, window_days: int = 30, check_id: str | None = None
    ) -> list[Row]:
        """Every result in the window, ordered for transition detection."""
        anchor = self.reference_time(company_id)
        if anchor is None:
            return []
        cutoff = anchor - timedelta(days=window_days)
        stmt = select(compliance_results).where(
            and_(
                compliance_results.c.company_id == company_id,
                compliance_results.c.collected_at >= cutoff,
            )
        )
        if check_id:
            stmt = stmt.where(compliance_results.c.check_id == check_id)
        return list(
            self.conn.execute(
                stmt.order_by(
                    compliance_results.c.device_id,
                    compliance_results.c.check_id,
                    compliance_results.c.collected_at,
                )
            ).fetchall()
        )

    def known_checks(self, company_id: str) -> list[Row]:
        """Distinct checks and their severity, for grounding "what is checked"."""
        stmt = (
            select(compliance_results.c.check_id, compliance_results.c.severity)
            .where(compliance_results.c.company_id == company_id)
            .distinct()
            .order_by(compliance_results.c.check_id)
        )
        return list(self.conn.execute(stmt).fetchall())
