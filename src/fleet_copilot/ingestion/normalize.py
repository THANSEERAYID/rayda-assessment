"""Turn a raw telemetry record into a :class:`DeviceSnapshot`.

The two platforms in the dataset (darwin, win32) publish identical key
structures, so normalisation is mostly about tolerating *absent* blocks rather
than reshaping them:

  * ``battery`` is missing entirely on 100 of 750 records. Three of those are
    Mac minis with no battery hardware at all (all 30 snapshots missing); the
    rest are dropped readings on laptops that do have one. Both arrive here as
    "no battery block", and the distinction is recovered later by looking at
    whether a device *ever* reports a battery.
  * ``network`` is missing on 16 records. Nothing downstream depends on it, so
    it is preserved in ``raw`` and otherwise ignored.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..domain.models import ComplianceCheck, DeviceSnapshot, InstalledSoftware


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 ``Z`` timestamp into a naive UTC datetime.

    Stored naive because SQLite has no timezone-aware type; everything in the
    dataset is UTC, and keeping one convention avoids mixed-awareness comparisons.
    """
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def make_snapshot_id(device_id: str, collected_at: datetime) -> str:
    """Stable id so re-ingesting the same file is idempotent."""
    return f"{device_id}:{collected_at.strftime('%Y%m%dT%H%M%S')}"


def normalize(record: dict[str, Any]) -> DeviceSnapshot:
    os_block = record.get("os") or {}
    identity = record.get("device_identity") or {}
    memory = record.get("memory") or {}
    volumes = record.get("disk_volumes") or []
    battery = record.get("battery") or {}

    primary_volume = _primary_volume(volumes)
    collected_at = parse_timestamp(record["collected_at"])

    ram_total = int(memory.get("total_memory_bytes") or memory.get("ram_bytes") or 0)
    ram_used = int(memory.get("used_memory_bytes") or 0)
    disk_size = int(primary_volume.get("size_bytes") or 0)
    disk_avail = int(primary_volume.get("available_bytes") or 0)

    return DeviceSnapshot(
        device_id=record["device_id"],
        company_id=record["company_id"],
        employee_id=record["employee_id"],
        collected_at=collected_at,
        platform=os_block.get("platform", "unknown"),
        os_product_name=os_block.get("product_name", "unknown"),
        os_product_version=os_block.get("product_version", "unknown"),
        model_name=identity.get("model_name", "unknown"),
        hostname=os_block.get("hostname", "unknown"),
        ram_total_bytes=ram_total,
        ram_used_bytes=ram_used,
        disk_size_bytes=disk_size,
        disk_available_bytes=disk_avail,
        disk_encrypted=bool(primary_volume.get("encrypted", False)),
        disk_mount_point=primary_volume.get("mount_point", ""),
        battery_present=bool(battery.get("battery_present", False)),
        battery_percentage=battery.get("percentage"),
        battery_condition=battery.get("condition"),
        battery_cycle_count=battery.get("cycle_count"),
        battery_full_charge_capacity=battery.get("full_charge_capacity"),
        battery_charging_status=battery.get("charging_status"),
        compliance=[
            ComplianceCheck(
                check_id=c["check_id"], status=c["status"], severity=c["severity"]
            )
            for c in (record.get("compliance_results") or [])
        ],
        software=[
            InstalledSoftware(
                name=s["name"], version=s["version"], publisher=s["publisher"]
            )
            for s in (record.get("installed_software") or [])
        ],
        raw=record,
    )


def _primary_volume(volumes: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick the system volume.

    Every record in the dataset has exactly one volume, but selecting the root
    mount explicitly keeps this correct if a device ever reports several.
    """
    if not volumes:
        return {}
    for volume in volumes:
        if volume.get("mount_point") in ("/", "C:\\"):
            return volume
    return volumes[0]
