"""Fleet querying — backs the ``query_devices`` and ``get_compliance_status`` tools.

Each matching device yields both a result row and the :class:`Evidence` records
that justify its inclusion, so a citation always exists for anything the agent
can say. Filters are declared as a typed object rather than free-form SQL: the
model chooses *which* filter to apply, never how the query is written.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection, Row

from ..config import settings
from ..domain.enums import AsOfMode, Severity
from ..domain.models import Evidence
from ..domain.text import format_device
from ..domain.versions import is_older_than, normalise_platform
from ..evidence.ledger import build_evidence
from ..storage.repositories.compliance import ComplianceRepository
from ..storage.repositories.snapshots import SnapshotRepository
from ..storage.repositories.software import SoftwareRepository

TOOL_QUERY_DEVICES = "query_devices"
TOOL_COMPLIANCE = "get_compliance_status"


class DeviceFilters(BaseModel):
    """Typed filter surface exposed to the model."""

    disk_free_pct_below: float | None = None
    disk_free_pct_above: float | None = None
    ram_used_pct_above: float | None = None
    battery_cycle_count_above: int | None = None
    battery_condition: str | None = None
    platform: str | None = Field(
        default=None, description="darwin | win32 | macOS | Windows"
    )
    os_older_than: str | None = Field(
        default=None,
        description=(
            "Version to compare against, e.g. '15' or '15.4'. Requires 'platform' "
            "because version ordering is only defined within a platform."
        ),
    )
    model_name: str | None = None
    hostname: str | None = Field(
        default=None,
        description=(
            "Match on the device's hostname, e.g. 'acme-macbook-4'. Substring "
            "match — this is how an administrator refers to a machine, so accept "
            "it wherever a serial would work."
        ),
    )
    employee_id: str | None = None
    device_ids: list[str] | None = None
    has_software: str | None = None
    compliance_check_id: str | None = None
    compliance_status: str | None = None
    compliance_severity: str | None = None


class DeviceMatch(BaseModel):
    device_id: str
    company_id: str
    employee_id: str
    model_name: str
    platform: str
    os_version: str
    hostname: str
    device_label: str
    collected_at: str
    disk_free_pct: float
    disk_size_bytes: int
    disk_available_bytes: int
    ram_used_pct: float
    battery_present: bool
    battery_percentage: int | None = None
    battery_condition: str | None = None
    battery_cycle_count: int | None = None
    matched_on: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


class QueryResult(BaseModel):
    matches: list[DeviceMatch] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    total_devices_considered: int = 0
    as_of_mode: str = AsOfMode.LATEST.value
    note: str | None = None


def _absence_evidence(tool: str, description: str, considered: int) -> Evidence:
    """A citable record that a query legitimately matched nothing.

    Absence is a finding, not a failed lookup — "no device is failing any
    high-severity check" is a real and useful answer. But every claim must cite
    something, so without a record for the empty case the agent has nothing to
    point at and the turn refuses for lack of evidence instead of reporting the
    absence. This gives it the fact to cite.
    """
    return build_evidence(
        tool=tool,
        field="query.match_count",
        value=0,
        detail={"query": description, "devices_considered": considered},
        # No device and no timestamp, so unrelated empty queries would otherwise
        # share one id; the query itself is what makes this record distinct.
        discriminator=description,
    )


class FleetQueryService:
    def __init__(self, conn: Connection, company_id: str) -> None:
        self.conn = conn
        self.company_id = company_id
        self.snapshots = SnapshotRepository(conn)
        self.compliance = ComplianceRepository(conn)
        self.software = SoftwareRepository(conn)

    # ------------------------------------------------------------------
    def query_devices(
        self,
        filters: DeviceFilters,
        *,
        mode: AsOfMode = AsOfMode.LATEST,
        window_days: int = 30,
    ) -> QueryResult:
        rows = self.snapshots.select(
            self.company_id,
            mode=mode,
            window_days=window_days,
            device_ids=filters.device_ids,
        )
        considered = len({r.device_id for r in rows})

        # Set-membership filters are resolved once rather than per row.
        software_devices = self._devices_with_software(filters.has_software)
        compliance_devices = self._devices_matching_compliance(filters)

        matches: list[DeviceMatch] = []
        evidence: list[Evidence] = []
        for row in rows:
            reasons = self._evaluate(row, filters, software_devices, compliance_devices)
            if reasons is None:
                continue
            records = self._evidence_for(row, reasons)
            evidence.extend(records)
            matches.append(
                DeviceMatch(
                    device_id=row.device_id,
                    company_id=row.company_id,
                    employee_id=row.employee_id,
                    model_name=row.model_name,
                    platform=row.platform,
                    os_version=row.os_product_version,
                    hostname=row.hostname,
                    device_label=format_device(row.hostname, row.model_name) or row.device_id,
                    collected_at=row.collected_at.isoformat(),
                    disk_free_pct=round(row.disk_free_pct, 2),
                    disk_size_bytes=row.disk_size_bytes,
                    disk_available_bytes=row.disk_available_bytes,
                    ram_used_pct=round(row.ram_used_pct, 2),
                    battery_present=bool(row.battery_present),
                    battery_percentage=row.battery_percentage,
                    battery_condition=row.battery_condition,
                    battery_cycle_count=row.battery_cycle_count,
                    matched_on=reasons,
                    evidence_ids=[r.evidence_id for r in records],
                )
            )

        note = None
        if not matches:
            note = (
                "No devices in this company match those criteria. This is a "
                "complete result, not a retrieval failure. Cite the "
                "query.match_count evidence record to state the absence."
            )
            described = json.dumps(
                filters.model_dump(exclude_none=True), sort_keys=True
            )
            evidence.append(
                _absence_evidence(TOOL_QUERY_DEVICES, described, considered)
            )
        return QueryResult(
            matches=matches,
            evidence=evidence,
            total_devices_considered=considered,
            as_of_mode=mode.value,
            note=note,
        )

    # ------------------------------------------------------------------
    def compliance_status(
        self,
        *,
        severity: str | None = None,
        status: str | None = None,
        check_id: str | None = None,
    ) -> QueryResult:
        """Latest compliance result per (device, check).

        Returns an explicit note when a filter matches nothing, so the agent can
        distinguish "nothing is failing" from "the query went wrong" — the
        dataset has zero high-severity failures, and reporting that correctly is
        the point.
        """
        rows = self.compliance.latest_results(
            self.company_id, severity=severity, status=status, check_id=check_id
        )
        evidence: list[Evidence] = []
        matches: list[DeviceMatch] = []
        snapshot_by_device = {
            r.device_id: r
            for r in self.snapshots.select(self.company_id, mode=AsOfMode.LATEST)
        }

        for row in rows:
            record = build_evidence(
                tool=TOOL_COMPLIANCE,
                field=f"compliance.{row.check_id}",
                value=row.status,
                device_id=row.device_id,
                device_label=format_device(
                    getattr(snapshot_by_device.get(row.device_id), "hostname", None),
                    getattr(snapshot_by_device.get(row.device_id), "model_name", None),
                ),
                snapshot_ts=row.collected_at,
                detail={"severity": row.severity, "check_id": row.check_id},
            )
            evidence.append(record)
            snap = snapshot_by_device.get(row.device_id)
            if snap is None:
                continue
            matches.append(
                DeviceMatch(
                    device_id=row.device_id,
                    company_id=self.company_id,
                    employee_id=snap.employee_id,
                    model_name=snap.model_name,
                    platform=snap.platform,
                    os_version=snap.os_product_version,
                    hostname=snap.hostname,
                    device_label=format_device(snap.hostname, snap.model_name) or row.device_id,
                    collected_at=row.collected_at.isoformat(),
                    disk_free_pct=round(snap.disk_free_pct, 2),
                    disk_size_bytes=snap.disk_size_bytes,
                    disk_available_bytes=snap.disk_available_bytes,
                    ram_used_pct=round(snap.ram_used_pct, 2),
                    battery_present=bool(snap.battery_present),
                    battery_percentage=snap.battery_percentage,
                    battery_condition=snap.battery_condition,
                    battery_cycle_count=snap.battery_cycle_count,
                    matched_on={
                        "check_id": row.check_id,
                        "status": row.status,
                        "severity": row.severity,
                    },
                    evidence_ids=[record.evidence_id],
                )
            )

        note = None
        if not matches:
            known = self.compliance.known_checks(self.company_id)
            catalogue = ", ".join(f"{c.check_id} ({c.severity})" for c in known)
            note = (
                "No compliance results match that filter. This is a complete "
                "result, not a retrieval failure — nothing is failing. Cite the "
                "query.match_count evidence record to state that. Checks "
                f"collected for this company: {catalogue or 'none'}."
            )
            described = json.dumps(
                {"severity": severity, "status": status, "check_id": check_id},
                sort_keys=True,
            )
            evidence.append(
                _absence_evidence(
                    TOOL_COMPLIANCE, described, len(snapshot_by_device)
                )
            )
        return QueryResult(
            matches=matches,
            evidence=evidence,
            total_devices_considered=len(snapshot_by_device),
            note=note,
        )

    # ------------------------------------------------------------------
    def _devices_with_software(self, name: str | None) -> set[str] | None:
        if not name:
            return None
        wanted = name.strip().lower()
        rows = self.software.latest_inventory(self.company_id)
        return {r.device_id for r in rows if wanted in r.name.lower()}

    def _devices_matching_compliance(self, filters: DeviceFilters) -> set[str] | None:
        if not any(
            [
                filters.compliance_check_id,
                filters.compliance_status,
                filters.compliance_severity,
            ]
        ):
            return None
        rows = self.compliance.latest_results(
            self.company_id,
            severity=filters.compliance_severity,
            status=filters.compliance_status,
            check_id=filters.compliance_check_id,
        )
        return {r.device_id for r in rows}

    def _evaluate(
        self,
        row: Row,
        filters: DeviceFilters,
        software_devices: set[str] | None,
        compliance_devices: set[str] | None,
    ) -> dict[str, Any] | None:
        """Return why a row matched, or ``None`` if it did not."""
        reasons: dict[str, Any] = {}

        if filters.disk_free_pct_below is not None:
            if row.disk_free_pct >= filters.disk_free_pct_below:
                return None
            reasons["disk_free_pct"] = round(row.disk_free_pct, 2)
        if filters.disk_free_pct_above is not None:
            if row.disk_free_pct <= filters.disk_free_pct_above:
                return None
            reasons["disk_free_pct"] = round(row.disk_free_pct, 2)
        if filters.ram_used_pct_above is not None:
            if row.ram_used_pct <= filters.ram_used_pct_above:
                return None
            reasons["ram_used_pct"] = round(row.ram_used_pct, 2)

        if filters.battery_cycle_count_above is not None:
            # A device with no battery cannot satisfy a battery filter.
            if row.battery_cycle_count is None:
                return None
            if row.battery_cycle_count <= filters.battery_cycle_count_above:
                return None
            reasons["battery_cycle_count"] = row.battery_cycle_count
        if filters.battery_condition is not None:
            if (row.battery_condition or "").lower() != filters.battery_condition.lower():
                return None
            reasons["battery_condition"] = row.battery_condition

        if filters.platform is not None:
            wanted = normalise_platform(filters.platform) or filters.platform
            if row.platform != wanted:
                return None
            reasons["platform"] = row.platform

        if filters.os_older_than is not None:
            if not is_older_than(row.platform, row.os_product_version, filters.os_older_than):
                return None
            reasons["os_version"] = row.os_product_version

        if filters.model_name is not None:
            if filters.model_name.lower() not in row.model_name.lower():
                return None
            reasons["model_name"] = row.model_name

        if filters.hostname is not None:
            if filters.hostname.lower() not in row.hostname.lower():
                return None
            reasons["hostname"] = row.hostname

        if filters.employee_id is not None:
            if row.employee_id != filters.employee_id:
                return None
            reasons["employee_id"] = row.employee_id

        if software_devices is not None:
            if row.device_id not in software_devices:
                return None
            reasons["has_software"] = filters.has_software

        if compliance_devices is not None:
            if row.device_id not in compliance_devices:
                return None
            reasons["compliance"] = {
                "check_id": filters.compliance_check_id,
                "status": filters.compliance_status,
                "severity": filters.compliance_severity,
            }

        if not reasons:
            # No filters supplied: the whole fleet matches, anchored on identity.
            reasons["all_devices"] = True
        return reasons

    def _evidence_for(self, row: Row, reasons: dict[str, Any]) -> list[Evidence]:
        """One evidence record per field the match actually depended on."""
        fields: dict[str, Any] = {}
        for key in reasons:
            if key == "disk_free_pct":
                fields["disk_free_pct"] = round(row.disk_free_pct, 2)
            elif key == "ram_used_pct":
                fields["ram_used_pct"] = round(row.ram_used_pct, 2)
            elif key == "battery_cycle_count":
                fields["battery.cycle_count"] = row.battery_cycle_count
            elif key == "battery_condition":
                fields["battery.condition"] = row.battery_condition
            elif key in ("platform", "os_version"):
                fields["os.product_version"] = row.os_product_version
            elif key == "model_name":
                fields["device_identity.model_name"] = row.model_name
            elif key == "hostname":
                fields["hostname"] = row.hostname
            elif key == "employee_id":
                fields["employee_id"] = row.employee_id
            elif key == "has_software":
                fields["installed_software"] = reasons["has_software"]
            elif key == "compliance":
                spec = reasons["compliance"]
                check = spec.get("check_id") or "any"
                fields[f"compliance.{check}"] = spec.get("status") or "matched"
            elif key == "all_devices":
                fields["device_identity.model_name"] = row.model_name

        # A match the agent cannot cite is worse than no match: it appears in
        # the result, gets counted, and then fails grounding with nothing to
        # point at. Anchor on identity so every matched device is always citable,
        # whatever it matched on.
        if not fields:
            fields["device_identity.model_name"] = row.model_name

        # Who holds the device is always citable. Without it the agent cannot
        # propose notify_employee at all: the handoff carries evidence records,
        # so an owner that never becomes one is invisible to the action agent
        # and the only person-targeted tool is unusable.
        fields.setdefault("employee_id", row.employee_id)

        # The two readings most actions turn on. Looking a device up by id
        # otherwise yields identity only, so "open a ticket for DEV-X, its disk
        # is nearly full" arrives at the action agent with nothing to cite for
        # the disk — and the proposal is refused for lack of evidence.
        fields.setdefault("disk_free_pct", round(row.disk_free_pct, 2))
        fields.setdefault("ram_used_pct", round(row.ram_used_pct, 2))

        return [
            build_evidence(
                tool=TOOL_QUERY_DEVICES,
                field=field,
                value=value,
                device_id=row.device_id,
                device_label=format_device(row.hostname, row.model_name),
                snapshot_ts=row.collected_at,
                detail={
                    "disk_free_pct": round(row.disk_free_pct, 2),
                    "ram_used_pct": round(row.ram_used_pct, 2),
                    "os_version": row.os_product_version,
                },
            )
            for field, value in fields.items()
        ]


def default_low_disk_threshold() -> float:
    return settings.disk_low_free_pct


def severity_values() -> list[str]:
    return [s.value for s in Severity]
