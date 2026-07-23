"""Shared API dependencies."""
from __future__ import annotations

from fastapi import HTTPException

from ..storage.db import connect
from ..storage.repositories.devices import DeviceRepository


def require_company(company_id: str) -> str:
    """Reject a tenant the dataset does not contain.

    Cheap, but it stops a malformed or probing ``company_id`` from reaching the
    agent and spawning a tool server bound to a tenant that does not exist.
    """
    with connect() as conn:
        if not DeviceRepository(conn).company_exists(company_id):
            raise HTTPException(status_code=404, detail="Unknown company.")
    return company_id
