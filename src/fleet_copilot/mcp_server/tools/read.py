"""Read-only telemetry tools.

Every tool returns an ``evidence`` array alongside its results. Those records
carry content-derived ids that the agent collects into the turn's ledger and that
the model must cite — no evidence, no claim.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ...domain.enums import AsOfMode, AuditEventType, Metric
from ...domain.errors import FleetCopilotError
from ...ingestion.normalize import parse_timestamp
from ...services.fleet_query import DeviceFilters, FleetQueryService
from ...services.history import HistoryService
from ...services.insights.registry import available_detectors, run_scan
from ...storage.repositories.audit import AuditRepository
from ...storage.repositories.snapshots import SnapshotRepository
from ...evidence.ledger import build_evidence
from ..context import get_context
from ..sql_guard import UnsafeQuery, validate_select


def _audit_call(conn, tool: str, args: dict[str, Any], result_count: int) -> None:
    ctx = get_context()
    AuditRepository(conn).record(
        event_type=AuditEventType.TOOL_CALL,
        company_id=ctx.company_id,
        thread_id=ctx.thread_id,
        summary=f"{tool} -> {result_count} result(s)",
        detail={"tool": tool, "args": args},
    )


def _emit(evidence) -> list[dict[str, Any]]:
    """Record evidence in the session ledger, then serialise it for the model.

    Registration must happen here, at the point the tool hands facts out, so the
    ledger is exactly the set of things the agent was actually shown.
    """
    get_context().ledger.extend(evidence)
    return [e.model_dump(mode="json") for e in evidence]


def _error(exc: FleetCopilotError) -> dict[str, Any]:
    """Tool errors are returned as data, not raised.

    The agent needs to *reason* about a refusal — and to surface the typed reason
    to the user — which it cannot do if the transport turns it into a stack trace.
    """
    return {"error": True, "reason": exc.reason.value, "message": exc.message}


def query_devices(
    disk_free_pct_below: float | None = None,
    disk_free_pct_above: float | None = None,
    ram_used_pct_above: float | None = None,
    battery_cycle_count_above: int | None = None,
    battery_condition: str | None = None,
    platform: str | None = None,
    os_older_than: str | None = None,
    model_name: str | None = None,
    hostname: str | None = None,
    employee_id: str | None = None,
    device_ids: list[str] | None = None,
    has_software: str | None = None,
    compliance_check_id: str | None = None,
    compliance_status: str | None = None,
    compliance_severity: str | None = None,
    mode: str = "latest",
    window_days: int = 30,
    company_id: str | None = None,
) -> dict[str, Any]:
    """Find devices in the current company matching hardware, OS or compliance criteria.

    Filters combine with AND. Omit every filter to list the whole fleet.

    ``mode`` selects which snapshots are considered:
      * ``latest`` (default) — each device's most recent snapshot. Use for
        "which devices are ..." questions about the current state.
      * ``window`` — every snapshot in the trailing ``window_days``. Use when the
        question is about behaviour over time.

    ``os_older_than`` requires ``platform``, because version ordering is only
    meaningful within a platform: "older than macOS 15" must not match Windows.

    ``hostname`` matches on the name an administrator actually uses for a machine
    (e.g. 'acme-macbook-4'). Use it when the question names a device that way
    rather than by serial.

    Do not set ``company_id`` — the tenant is fixed by the session.
    """
    ctx = get_context()
    args = {k: v for k, v in locals().items() if k != "ctx" and v is not None}
    with ctx.connection() as conn:
        try:
            ctx.guard(conn).assert_company(company_id)
            if os_older_than and not platform:
                return {
                    "error": True,
                    "reason": "invalid_arguments",
                    "message": (
                        "os_older_than requires platform (e.g. platform='macOS', "
                        "os_older_than='15'), because version ordering differs by platform."
                    ),
                }
            filters = DeviceFilters(
                disk_free_pct_below=disk_free_pct_below,
                disk_free_pct_above=disk_free_pct_above,
                ram_used_pct_above=ram_used_pct_above,
                battery_cycle_count_above=battery_cycle_count_above,
                battery_condition=battery_condition,
                platform=platform,
                os_older_than=os_older_than,
                model_name=model_name,
                hostname=hostname,
                employee_id=employee_id,
                device_ids=device_ids,
                has_software=has_software,
                compliance_check_id=compliance_check_id,
                compliance_status=compliance_status,
                compliance_severity=compliance_severity,
            )
            if device_ids:
                ctx.guard(conn).assert_devices(device_ids)
            result = FleetQueryService(conn, ctx.company_id).query_devices(
                filters, mode=AsOfMode(mode), window_days=window_days
            )
            _audit_call(conn, "query_devices", args, len(result.matches))
            return {
                "matches": [m.model_dump() for m in result.matches],
                "match_count": len(result.matches),
                "total_devices_considered": result.total_devices_considered,
                "as_of_mode": result.as_of_mode,
                "note": result.note,
                "evidence": _emit(result.evidence),
            }
        except FleetCopilotError as exc:
            return _error(exc)


def get_compliance_status(
    severity: str | None = None,
    status: str | None = None,
    check_id: str | None = None,
    company_id: str | None = None,
) -> dict[str, Any]:
    """Latest compliance result per device and check for the current company.

    Filter by ``severity`` (low/medium/high), ``status`` (pass/fail), or a
    specific ``check_id``.

    An empty result is a real answer, not a failure: it means nothing matches.
    The ``note`` field then lists which checks this company actually collects, so
    a genuine "nothing is failing" can be reported with confidence.

    Do not set ``company_id`` — the tenant is fixed by the session.
    """
    ctx = get_context()
    args = {"severity": severity, "status": status, "check_id": check_id}
    with ctx.connection() as conn:
        try:
            ctx.guard(conn).assert_company(company_id)
            result = FleetQueryService(conn, ctx.company_id).compliance_status(
                severity=severity, status=status, check_id=check_id
            )
            _audit_call(conn, "get_compliance_status", args, len(result.matches))
            return {
                "matches": [m.model_dump() for m in result.matches],
                "match_count": len(result.matches),
                "note": result.note,
                "evidence": _emit(result.evidence),
            }
        except FleetCopilotError as exc:
            return _error(exc)


def get_device_history(
    device_id: str,
    metric: str,
    window_days: int = 30,
    company_id: str | None = None,
) -> dict[str, Any]:
    """Time series for one metric on one device, with trend statistics.

    ``metric`` is one of: disk_free_pct, ram_used_pct, battery_percentage,
    battery_cycle_count, battery_full_charge_capacity, battery_condition.

    The ``summary`` block carries first/last/min/max, absolute and percentage
    change, and a least-squares ``slope_per_day``. Use those figures rather than
    computing trends yourself.

    Do not set ``company_id`` — the tenant is fixed by the session.
    """
    ctx = get_context()
    with ctx.connection() as conn:
        try:
            ctx.guard(conn).assert_company(company_id)
            ctx.guard(conn).assert_device(device_id)
            try:
                parsed_metric = Metric(metric)
            except ValueError:
                return {
                    "error": True,
                    "reason": "invalid_arguments",
                    "message": (
                        f"Unknown metric '{metric}'. Valid metrics: "
                        + ", ".join(m.value for m in Metric)
                    ),
                }
            result = HistoryService(conn, ctx.company_id).get_history(
                device_id, parsed_metric, window_days=window_days
            )
            _audit_call(
                conn,
                "get_device_history",
                {"device_id": device_id, "metric": metric, "window_days": window_days},
                len(result.points),
            )
            return {
                "device_id": result.device_id,
                "metric": result.metric,
                "window_days": result.window_days,
                "points": [p.model_dump() for p in result.points],
                "summary": result.summary.model_dump(),
                "note": result.note,
                "evidence": _emit(result.evidence),
            }
        except FleetCopilotError as exc:
            return _error(exc)


def get_device_snapshot(
    device_id: str,
    at: str | None = None,
    company_id: str | None = None,
) -> dict[str, Any]:
    """The raw telemetry record for one device — what a citation resolves to.

    ``at`` is an optional ISO-8601 timestamp; the newest snapshot at or before
    that instant is returned (never one from after it). Omit ``at`` for the
    latest snapshot.

    Do not set ``company_id`` — the tenant is fixed by the session.
    """
    ctx = get_context()
    with ctx.connection() as conn:
        try:
            ctx.guard(conn).assert_company(company_id)
            ctx.guard(conn).assert_device(device_id)
            timestamp: datetime | None = None
            if at:
                try:
                    timestamp = parse_timestamp(at)
                except ValueError:
                    return {
                        "error": True,
                        "reason": "invalid_arguments",
                        "message": f"Could not parse timestamp '{at}'. Use ISO-8601.",
                    }
            raw = SnapshotRepository(conn).raw_snapshot(
                ctx.company_id, device_id, timestamp
            )
            _audit_call(
                conn, "get_device_snapshot", {"device_id": device_id, "at": at}, 1 if raw else 0
            )
            if raw is None:
                return {
                    "error": True,
                    "reason": "unanswerable_from_data",
                    "message": f"No snapshot for {device_id} at or before that time.",
                }
            return {"device_id": device_id, "snapshot": raw}
        except FleetCopilotError as exc:
            return _error(exc)


def run_insight_scan(
    detectors: list[str] | None = None,
    window_days: int = 30,
    company_id: str | None = None,
) -> dict[str, Any]:
    """Run deterministic trend detectors over the company's telemetry window.

    Available detectors: disk_pressure, ram_pressure, battery_eol,
    compliance_drift, unapproved_software. Omit ``detectors`` to run all of them.

    Every figure in a finding's ``metrics`` is computed from the telemetry. Use
    them as given and explain what they mean — do not recompute or estimate
    trends yourself.

    Do not set ``company_id`` — the tenant is fixed by the session.
    """
    ctx = get_context()
    with ctx.connection() as conn:
        try:
            ctx.guard(conn).assert_company(company_id)
            try:
                output = run_scan(
                    conn, ctx.company_id, detectors=detectors, window_days=window_days
                )
            except ValueError as exc:
                return {
                    "error": True,
                    "reason": "invalid_arguments",
                    "message": str(exc),
                }
            _audit_call(
                conn,
                "run_insight_scan",
                {"detectors": detectors, "window_days": window_days},
                len(output.findings),
            )
            return {
                "findings": [f.model_dump(mode="json") for f in output.findings],
                "finding_count": len(output.findings),
                "detectors_available": available_detectors(),
                "window_days": window_days,
                "note": None
                if output.findings
                else "No findings. This is a complete result, not a failure.",
                "evidence": _emit(output.evidence),
            }
        except FleetCopilotError as exc:
            return _error(exc)


def list_fleet_summary(company_id: str | None = None) -> dict[str, Any]:
    """Counts and inventory for the current company — a cheap orientation call.

    Useful as a first step to learn how many devices exist, which platforms and
    OS versions are present, and which compliance checks are collected.

    Do not set ``company_id`` — the tenant is fixed by the session.
    """
    ctx = get_context()
    with ctx.connection() as conn:
        try:
            ctx.guard(conn).assert_company(company_id)
            from ...storage.repositories.compliance import ComplianceRepository
            from ...storage.repositories.devices import DeviceRepository

            devices = DeviceRepository(conn).list_devices(ctx.company_id)
            repo = SnapshotRepository(conn)
            latest = repo.select(ctx.company_id, mode=AsOfMode.LATEST)
            checks = ComplianceRepository(conn).known_checks(ctx.company_id)
            reference = repo.reference_time(ctx.company_id)

            platforms: dict[str, int] = {}
            os_versions: dict[str, int] = {}
            for row in latest:
                platforms[row.platform] = platforms.get(row.platform, 0) + 1
                key = f"{row.os_product_name} {row.os_product_version}"
                os_versions[key] = os_versions.get(key, 0) + 1

            _audit_call(conn, "list_fleet_summary", {}, len(devices))
            return {
                "company_id": ctx.company_id,
                "device_count": len(devices),
                "employee_count": len({d.employee_id for d in devices}),
                "platforms": platforms,
                "os_versions": os_versions,
                "compliance_checks": [
                    {"check_id": c.check_id, "severity": c.severity} for c in checks
                ],
                "latest_telemetry_at": reference.isoformat() if reference else None,
                "models": sorted({d.model_name for d in devices}),
            }
        except FleetCopilotError as exc:
            return _error(exc)


def _tenant_views(conn, company_id: str) -> None:
    """Shadow each queryable table with a view holding only this tenant's rows.

    Temporary views take precedence over base tables for the connection that
    created them, so a query written against ``snapshots`` reads the view. This
    is the layer that does not depend on parsing the model's SQL correctly:
    even a statement the guard wrongly admits cannot see another company.

    ``companies`` is filtered to the bound row for the same reason — the tenant
    should not be able to enumerate the others.
    """
    scoped = {
        "snapshots": "company_id",
        "devices": "company_id",
        "employees": "company_id",
        "compliance_results": "company_id",
        "installed_software": "company_id",
        "companies": "company_id",
    }
    for table, column in scoped.items():
        conn.exec_driver_sql(
            f"CREATE TEMP VIEW {table} AS "
            f"SELECT * FROM public.{table} WHERE {column} = '{company_id}'"
            if conn.dialect.name == "postgresql"
            else f"CREATE TEMP VIEW {table} AS "
            f"SELECT * FROM main.{table} WHERE {column} = '{company_id}'"
        )


def run_read_query(
    sql: str,
    limit: int = 200,
    company_id: str | None = None,
) -> dict[str, Any]:
    """Answer a fleet question with a read-only SQL query over the telemetry.

    Use this only when no other tool fits — `query_devices`, `get_compliance_status`
    and `run_insight_scan` return computed metrics and are always preferable.
    This is for shapes they do not cover: aggregates, group-bys, joins across
    software and compliance, "how many per platform" style questions.

    Rules enforced by the server, not by you:
      * one SELECT only — no writes, no DDL, no multiple statements;
      * these tables only: snapshots, devices, employees, compliance_results,
        installed_software, companies;
      * plain table names, never schema-qualified;
      * results are already restricted to the current company, so do NOT add a
        `company_id` filter and do not pass `company_id`.

    Each returned row becomes a citable evidence record, so figures taken from
    this result can be used in an answer like any other reading.

    Prefer explicit columns over `SELECT *`, and aggregate in SQL rather than
    returning many rows and counting them yourself.
    """
    ctx = get_context()
    args = {"sql": sql, "limit": limit}
    with ctx.connection() as conn:
        try:
            ctx.guard(conn).assert_company(company_id)
            statement = validate_select(sql)
            capped = max(1, min(int(limit), 500))

            _tenant_views(conn, ctx.company_id)
            if conn.dialect.name == "postgresql":
                # A runaway join must not hold the connection open.
                conn.exec_driver_sql("SET LOCAL statement_timeout = '5s'")

            result = conn.exec_driver_sql(
                f"SELECT * FROM ({statement}) AS agent_query LIMIT {capped + 1}"
            )
            columns = list(result.keys())
            fetched = result.fetchall()
        except UnsafeQuery as exc:
            _audit_call(conn, "run_read_query", args, 0)
            return {
                "error": True,
                "reason": "invalid_arguments",
                "message": str(exc),
            }
        except FleetCopilotError as exc:
            return _error(exc)
        except Exception as exc:  # malformed SQL, unknown column, timeout
            _audit_call(conn, "run_read_query", args, 0)
            return {
                "error": True,
                "reason": "query_failed",
                "message": f"The query could not be run: {type(exc).__name__}: {exc}",
            }

        truncated = len(fetched) > capped
        rows = [dict(zip(columns, row)) for row in fetched[:capped]]
        _audit_call(conn, "run_read_query", args, len(rows))

    evidence = _query_evidence(rows, columns, statement)
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "note": (
            f"Only the first {capped} rows are shown. Aggregate in SQL to see all of it."
            if truncated
            else None
        ),
        "evidence": _emit(evidence),
    }


def _query_evidence(rows: list[dict], columns: list[str], statement: str) -> list:
    """One citable record per row, plus one for the count.

    The row's own columns go into ``detail`` so every figure in it is checkable
    by the grounding validator — a claim quoting a number from this result has
    to find that number in the record it cites.

    The count record exists so "no rows matched" is citable too. Absence is a
    finding, and an uncitable one would force a refusal.
    """
    records = [
        build_evidence(
            tool="run_read_query",
            field="query.row_count",
            value=len(rows),
            detail={"sql": statement[:400]},
        )
    ]
    for row in rows[:100]:
        device_id = row.get("device_id")
        records.append(
            build_evidence(
                tool="run_read_query",
                field="query.result",
                value=", ".join(f"{k}={row[k]}" for k in columns if row.get(k) is not None),
                device_id=str(device_id) if device_id else None,
                detail={k: v for k, v in row.items() if v is not None},
            )
        )
    return records
