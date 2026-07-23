"""Tenant listing — the source for the UI's company selector."""
from __future__ import annotations

from fastapi import APIRouter

from ...storage.db import connect
from ...storage.repositories.devices import DeviceRepository
from ..schemas import CompanyOut

router = APIRouter(tags=["tenants"])


@router.get("/companies", response_model=list[CompanyOut])
def list_companies() -> list[CompanyOut]:
    with connect() as conn:
        repo = DeviceRepository(conn)
        return [
            CompanyOut(
                company_id=row.company_id,
                name=row.name,
                device_count=len(repo.list_devices(row.company_id)),
            )
            for row in repo.list_companies()
        ]
