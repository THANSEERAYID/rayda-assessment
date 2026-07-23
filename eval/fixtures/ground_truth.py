"""Ground truth computed independently of the application code.

Every expectation here is derived by reading the raw NDJSON directly with plain
Python — no repositories, no services, no detectors. That independence is the
whole point: asserting the agent agrees with the same code that produced its
answer proves nothing. If a repository query and this module disagree, one of
them is wrong, and the test says so.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from fleet_copilot.config import settings


def _load() -> list[dict]:
    path = Path(settings.dataset_path)
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _disk_free_pct(record: dict) -> float:
    volume = (record.get("disk_volumes") or [{}])[0]
    size = volume.get("size_bytes") or 0
    return 100.0 * (volume.get("available_bytes") or 0) / size if size else 0.0


def _ram_used_pct(record: dict) -> float:
    memory = record.get("memory") or {}
    total = memory.get("total_memory_bytes") or 0
    return 100.0 * (memory.get("used_memory_bytes") or 0) / total if total else 0.0


@dataclass
class GroundTruth:
    records: list[dict]
    by_company: dict[str, list[dict]] = field(default_factory=dict)
    by_device: dict[str, list[dict]] = field(default_factory=dict)
    device_company: dict[str, str] = field(default_factory=dict)

    @classmethod
    def build(cls) -> "GroundTruth":
        records = _load()
        truth = cls(records=records)
        for record in records:
            truth.by_company.setdefault(record["company_id"], []).append(record)
            truth.by_device.setdefault(record["device_id"], []).append(record)
            truth.device_company[record["device_id"]] = record["company_id"]
        for series in truth.by_device.values():
            series.sort(key=lambda r: r["collected_at"])
        return truth

    # -- basics ----------------------------------------------------------
    @property
    def companies(self) -> set[str]:
        return set(self.by_company)

    def devices(self, company_id: str) -> set[str]:
        return {r["device_id"] for r in self.by_company[company_id]}

    def employees(self, company_id: str) -> set[str]:
        return {r["employee_id"] for r in self.by_company[company_id]}

    def latest(self, company_id: str) -> dict[str, dict]:
        """Newest snapshot per device."""
        return {
            device_id: self.by_device[device_id][-1]
            for device_id in self.devices(company_id)
        }

    # -- expectations ----------------------------------------------------
    def devices_below_disk_free(self, company_id: str, threshold: float) -> set[str]:
        return {
            device_id
            for device_id, record in self.latest(company_id).items()
            if _disk_free_pct(record) < threshold
        }

    def devices_ram_sustained(
        self, company_id: str, threshold: float, ratio: float
    ) -> set[str]:
        matched = set()
        for device_id in self.devices(company_id):
            series = [_ram_used_pct(r) for r in self.by_device[device_id]]
            if series and sum(1 for v in series if v > threshold) / len(series) >= ratio:
                matched.add(device_id)
        return matched

    def compliance_failures(
        self, company_id: str, severity: str | None = None
    ) -> set[str]:
        """Devices whose newest result for a check is a failure."""
        matched = set()
        for device_id, record in self.latest(company_id).items():
            for check in record.get("compliance_results") or []:
                if severity and check["severity"] != severity:
                    continue
                if check["status"] == "fail":
                    matched.add(device_id)
        return matched

    def compliance_regressions(self, company_id: str) -> set[tuple[str, str]]:
        """(device_id, check_id) pairs that went pass -> fail during the window."""
        regressed = set()
        for device_id in self.devices(company_id):
            history: dict[str, list[str]] = defaultdict(list)
            for record in self.by_device[device_id]:
                for check in record.get("compliance_results") or []:
                    history[check["check_id"]].append(check["status"])
            for check_id, statuses in history.items():
                if any(
                    statuses[i - 1] == "pass" and statuses[i] == "fail"
                    for i in range(1, len(statuses))
                ):
                    regressed.add((device_id, check_id))
        return regressed

    def battery_eol(
        self, company_id: str, cycle_threshold: int, decline_pct: float
    ) -> set[str]:
        """Devices where at least two independent battery signals agree."""
        matched = set()
        for device_id in self.devices(company_id):
            readings = [
                r["battery"]
                for r in self.by_device[device_id]
                if (r.get("battery") or {}).get("battery_present")
            ]
            if not readings:
                continue
            conditions = {b.get("condition") for b in readings if b.get("condition")}
            capacities = [
                float(b["full_charge_capacity"])
                for b in readings
                if b.get("full_charge_capacity") is not None
            ]
            cycles = readings[-1].get("cycle_count")

            decline = 0.0
            if len(capacities) >= 2 and capacities[0]:
                decline = 100.0 * (capacities[0] - capacities[-1]) / capacities[0]

            signals = [
                bool(conditions - {"Normal"}),
                cycles is not None and cycles >= cycle_threshold,
                decline >= decline_pct,
            ]
            if sum(signals) >= 2:
                matched.add(device_id)
        return matched

    def devices_with_software(self, company_id: str, names: set[str]) -> set[str]:
        matched = set()
        for device_id, record in self.latest(company_id).items():
            installed = {s["name"] for s in record.get("installed_software") or []}
            if installed & names:
                matched.add(device_id)
        return matched

    def devices_on_platform_older_than(
        self, company_id: str, platform: str, major: int
    ) -> set[str]:
        """macOS-style dotted versions only; Windows is compared elsewhere."""
        matched = set()
        for device_id, record in self.latest(company_id).items():
            os_block = record.get("os") or {}
            if os_block.get("platform") != platform:
                continue
            version = os_block.get("product_version", "")
            head = version.split(".")[0].split()[0]
            if head.isdigit() and int(head) < major:
                matched.add(device_id)
        return matched

    def devices_without_battery(self, company_id: str) -> set[str]:
        """Devices that never report a battery — desktops, not dropped readings."""
        matched = set()
        for device_id in self.devices(company_id):
            if not any(
                (r.get("battery") or {}).get("battery_present")
                for r in self.by_device[device_id]
            ):
                matched.add(device_id)
        return matched


@lru_cache(maxsize=1)
def ground_truth() -> GroundTruth:
    return GroundTruth.build()
