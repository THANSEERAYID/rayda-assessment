"""Load the dataset into the database.

Idempotent: snapshot ids are derived from ``device_id`` + ``collected_at``, and
telemetry tables are cleared before insert, so re-running never duplicates rows.
Operational tables (actions, audit, threads) are left untouched.
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import Engine, delete, insert

from ..domain.models import DeviceSnapshot
from ..storage.db import create_schema, get_engine
from ..storage.tables import (
    companies,
    compliance_results,
    devices,
    employees,
    installed_software,
    snapshots,
)
from .loader import load_snapshots
from .normalize import make_snapshot_id

# The dataset carries ids but no display names.
COMPANY_NAMES = {
    "acme-001": "Acme",
    "globex-002": "Globex",
    "initech-003": "Initech",
}


def _company_name(company_id: str) -> str:
    if company_id in COMPANY_NAMES:
        return COMPANY_NAMES[company_id]
    stem = company_id.rsplit("-", 1)[0]
    return stem.replace("-", " ").title()


def ingest(
    engine: Engine | None = None,
    path: Path | None = None,
    records: list[DeviceSnapshot] | None = None,
) -> dict[str, int]:
    """Populate telemetry tables. Returns row counts per table."""
    engine = engine or get_engine()
    create_schema(engine)
    rows = records if records is not None else load_snapshots(path)

    company_rows: dict[str, dict] = {}
    employee_rows: dict[str, dict] = {}
    device_rows: dict[str, dict] = {}
    snapshot_rows: list[dict] = []
    compliance_rows: list[dict] = []
    software_rows: list[dict] = []

    for snap in rows:
        company_rows.setdefault(
            snap.company_id,
            {"company_id": snap.company_id, "name": _company_name(snap.company_id)},
        )
        employee_rows.setdefault(
            snap.employee_id,
            {"employee_id": snap.employee_id, "company_id": snap.company_id},
        )
        # Device identity is taken from the newest snapshot seen for that device.
        device_rows[snap.device_id] = {
            "device_id": snap.device_id,
            "company_id": snap.company_id,
            "employee_id": snap.employee_id,
            "model_name": snap.model_name,
            "platform": snap.platform,
            "hostname": snap.hostname,
        }

        snapshot_id = make_snapshot_id(snap.device_id, snap.collected_at)
        snapshot_rows.append(
            {
                "snapshot_id": snapshot_id,
                "device_id": snap.device_id,
                "company_id": snap.company_id,
                "employee_id": snap.employee_id,
                "collected_at": snap.collected_at,
                "platform": snap.platform,
                "os_product_name": snap.os_product_name,
                "os_product_version": snap.os_product_version,
                "model_name": snap.model_name,
                "hostname": snap.hostname,
                "ram_total_bytes": snap.ram_total_bytes,
                "ram_used_bytes": snap.ram_used_bytes,
                "ram_used_pct": snap.ram_used_pct,
                "disk_size_bytes": snap.disk_size_bytes,
                "disk_available_bytes": snap.disk_available_bytes,
                "disk_free_pct": snap.disk_free_pct,
                "disk_encrypted": snap.disk_encrypted,
                "disk_mount_point": snap.disk_mount_point,
                "battery_present": snap.battery_present,
                "battery_percentage": snap.battery_percentage,
                "battery_condition": snap.battery_condition,
                "battery_cycle_count": snap.battery_cycle_count,
                "battery_full_charge_capacity": snap.battery_full_charge_capacity,
                "battery_charging_status": snap.battery_charging_status,
                "raw": json.dumps(snap.raw, separators=(",", ":")),
            }
        )
        for check in snap.compliance:
            compliance_rows.append(
                {
                    "snapshot_id": snapshot_id,
                    "device_id": snap.device_id,
                    "company_id": snap.company_id,
                    "collected_at": snap.collected_at,
                    "check_id": check.check_id,
                    "status": check.status.value,
                    "severity": check.severity.value,
                }
            )
        for app in snap.software:
            software_rows.append(
                {
                    "snapshot_id": snapshot_id,
                    "device_id": snap.device_id,
                    "company_id": snap.company_id,
                    "collected_at": snap.collected_at,
                    "name": app.name,
                    "version": app.version,
                    "publisher": app.publisher,
                }
            )

    with engine.begin() as conn:
        # Children first — foreign keys are enforced on SQLite.
        conn.execute(delete(installed_software))
        conn.execute(delete(compliance_results))
        conn.execute(delete(snapshots))
        conn.execute(delete(devices))
        conn.execute(delete(employees))
        conn.execute(delete(companies))

        conn.execute(insert(companies), list(company_rows.values()))
        conn.execute(insert(employees), list(employee_rows.values()))
        conn.execute(insert(devices), list(device_rows.values()))
        conn.execute(insert(snapshots), snapshot_rows)
        if compliance_rows:
            conn.execute(insert(compliance_results), compliance_rows)
        if software_rows:
            conn.execute(insert(installed_software), software_rows)

    return {
        "companies": len(company_rows),
        "employees": len(employee_rows),
        "devices": len(device_rows),
        "snapshots": len(snapshot_rows),
        "compliance_results": len(compliance_rows),
        "installed_software": len(software_rows),
    }


def main() -> None:  # pragma: no cover - CLI entry point
    counts = ingest()
    width = max(len(k) for k in counts)
    print("Ingested:")
    for table, count in counts.items():
        print(f"  {table:<{width}}  {count}")


if __name__ == "__main__":  # pragma: no cover
    main()
