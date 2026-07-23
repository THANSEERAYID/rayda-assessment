"""Installed-software queries."""
from __future__ import annotations

from sqlalchemy import and_, func, select
from sqlalchemy.engine import Connection, Row

from ..tables import installed_software


class SoftwareRepository:
    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def latest_inventory(
        self, company_id: str, *, names: list[str] | None = None
    ) -> list[Row]:
        """Software present on each device's newest snapshot.

        Ranking is per device (not per device+app) so the result is the inventory
        of one specific snapshot — an app uninstalled last week must not linger.
        """
        newest = (
            select(
                installed_software.c.device_id,
                func.max(installed_software.c.collected_at).label("collected_at"),
            )
            .where(installed_software.c.company_id == company_id)
            .group_by(installed_software.c.device_id)
            .subquery()
        )
        stmt = select(installed_software).join(
            newest,
            and_(
                installed_software.c.device_id == newest.c.device_id,
                installed_software.c.collected_at == newest.c.collected_at,
            ),
        )
        if names:
            stmt = stmt.where(installed_software.c.name.in_(names))
        return list(
            self.conn.execute(
                stmt.order_by(installed_software.c.device_id, installed_software.c.name)
            ).fetchall()
        )

    def distinct_names(self, company_id: str) -> list[str]:
        stmt = (
            select(installed_software.c.name)
            .where(installed_software.c.company_id == company_id)
            .distinct()
            .order_by(installed_software.c.name)
        )
        return [r[0] for r in self.conn.execute(stmt)]
