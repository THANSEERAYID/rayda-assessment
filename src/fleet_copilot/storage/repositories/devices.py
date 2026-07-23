"""Device, employee and tenant reference lookups."""
from __future__ import annotations

from sqlalchemy import and_, select
from sqlalchemy.engine import Connection, Row

from ..tables import companies, devices, employees


class DeviceRepository:
    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def list_companies(self) -> list[Row]:
        return list(
            self.conn.execute(select(companies).order_by(companies.c.name)).fetchall()
        )

    def company_exists(self, company_id: str) -> bool:
        return (
            self.conn.execute(
                select(companies.c.company_id).where(
                    companies.c.company_id == company_id
                )
            ).first()
            is not None
        )

    def list_devices(self, company_id: str) -> list[Row]:
        return list(
            self.conn.execute(
                select(devices)
                .where(devices.c.company_id == company_id)
                .order_by(devices.c.device_id)
            ).fetchall()
        )

    def get_device(self, company_id: str, device_id: str) -> Row | None:
        """Tenant-scoped fetch. Returns ``None`` for another tenant's device."""
        return self.conn.execute(
            select(devices).where(
                and_(
                    devices.c.company_id == company_id,
                    devices.c.device_id == device_id,
                )
            )
        ).first()

    def device_company(self, device_id: str) -> str | None:
        """Unscoped owner lookup.

        Used only to tell "belongs to another tenant" apart from "does not exist"
        so the two can be audited differently. The distinction must never reach
        the user — see ``services.tenant``.
        """
        return self.conn.execute(
            select(devices.c.company_id).where(devices.c.device_id == device_id)
        ).scalar()

    def employee_company(self, employee_id: str) -> str | None:
        return self.conn.execute(
            select(employees.c.company_id).where(
                employees.c.employee_id == employee_id
            )
        ).scalar()

    def list_employees(self, company_id: str) -> list[Row]:
        return list(
            self.conn.execute(
                select(employees)
                .where(employees.c.company_id == company_id)
                .order_by(employees.c.employee_id)
            ).fetchall()
        )
