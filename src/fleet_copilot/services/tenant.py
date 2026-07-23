"""Tenant ownership validation.

Every tool argument that names an entity — ``device_id``, ``employee_id`` — is
checked here before any query or write runs. The bound tenant always comes from
the session, never from the model, so a request for another company's device
fails loudly instead of quietly returning nothing.

Two deliberate choices:

*Fail loudly, not silently.* A cross-tenant reference raises
:class:`TenantViolation` and writes an audit record. Returning an empty result
would leave no trace that the attempt happened.

*Do not leak existence.* The audit log distinguishes "device belongs to another
tenant" from "device does not exist", but the message handed back to the caller
is identical for both. Saying "that device is not yours" would confirm the id is
real, which is itself a cross-tenant disclosure.
"""
from __future__ import annotations

from sqlalchemy.engine import Connection

from ..domain.enums import AuditEventType
from ..domain.errors import TenantViolation, UnknownEntity
from ..storage.repositories.audit import AuditRepository
from ..storage.repositories.devices import DeviceRepository

# Identical for "wrong tenant" and "no such device" — see module docstring.
_OPAQUE_DEVICE = "No such device is visible in the selected company."
_OPAQUE_EMPLOYEE = "No such employee is visible in the selected company."


class TenantGuard:
    def __init__(self, conn: Connection, company_id: str, thread_id: str | None = None):
        self.conn = conn
        self.company_id = company_id
        self.thread_id = thread_id
        self._devices = DeviceRepository(conn)
        self._audit = AuditRepository(conn)

    # -- assertions ------------------------------------------------------
    def assert_company(self, requested: str | None) -> None:
        """Reject a tool call that names a different tenant than the session.

        The model is never given the tenant; if a ``company_id`` argument turns
        up at all and disagrees with the binding, that is an attempted escape.
        """
        if requested is None or requested == self.company_id:
            return
        self._audit.record(
            event_type=AuditEventType.TENANT_VIOLATION,
            company_id=self.company_id,
            thread_id=self.thread_id,
            summary=(
                f"Rejected call scoped to '{requested}' while bound to "
                f"'{self.company_id}'"
            ),
            detail={"requested_company_id": requested, "bound_company_id": self.company_id},
        )
        raise TenantViolation(
            "This session can only access the selected company's fleet.",
            requested_company_id=requested,
        )

    def assert_device(self, device_id: str) -> None:
        if self._devices.get_device(self.company_id, device_id) is not None:
            return
        owner = self._devices.device_company(device_id)
        if owner is None:
            self._audit.record(
                event_type=AuditEventType.TOOL_ERROR,
                company_id=self.company_id,
                thread_id=self.thread_id,
                summary=f"Unknown device '{device_id}'",
                detail={"device_id": device_id},
            )
            raise UnknownEntity(_OPAQUE_DEVICE, device_id=device_id)
        self._audit.record(
            event_type=AuditEventType.TENANT_VIOLATION,
            company_id=self.company_id,
            thread_id=self.thread_id,
            summary=f"Blocked cross-tenant access to device '{device_id}'",
            detail={"device_id": device_id, "owner_company_id": owner},
        )
        raise TenantViolation(_OPAQUE_DEVICE, device_id=device_id)

    def assert_employee(self, employee_id: str) -> None:
        owner = self._devices.employee_company(employee_id)
        if owner == self.company_id:
            return
        if owner is None:
            self._audit.record(
                event_type=AuditEventType.TOOL_ERROR,
                company_id=self.company_id,
                thread_id=self.thread_id,
                summary=f"Unknown employee '{employee_id}'",
                detail={"employee_id": employee_id},
            )
            raise UnknownEntity(_OPAQUE_EMPLOYEE, employee_id=employee_id)
        self._audit.record(
            event_type=AuditEventType.TENANT_VIOLATION,
            company_id=self.company_id,
            thread_id=self.thread_id,
            summary=f"Blocked cross-tenant access to employee '{employee_id}'",
            detail={"employee_id": employee_id, "owner_company_id": owner},
        )
        raise TenantViolation(_OPAQUE_EMPLOYEE, employee_id=employee_id)

    def assert_devices(self, device_ids: list[str]) -> None:
        for device_id in device_ids:
            self.assert_device(device_id)
