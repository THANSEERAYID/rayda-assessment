"""Resolve a model's chart requests into render-ready data.

Every branch here follows the same rule the claim validator applies: if a
request can't be resolved against what was actually retrieved this turn, it is
dropped, not patched or approximated. A dashboard that silently shows the wrong
device's history would be worse than no chart at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.charts import ChartData, ChartPoint, ChartRequest, ChartType
from ..domain.enums import FindingType
from ..domain.models import Finding
from ..domain.text import format_finding_type, unit_for_metric
from .ledger import EvidenceLedger

_SEVERITY_ORDER = ["high", "medium", "low"]
_COUNT_UNIT_FINDINGS = "findings"
_COUNT_UNIT_DEVICES = "devices"


@dataclass
class ChartResolution:
    charts: list[ChartData] = field(default_factory=list)
    rejected: list[tuple[ChartRequest, str]] = field(default_factory=list)


def resolve_charts(
    requests: list[ChartRequest],
    *,
    ledger: EvidenceLedger,
    findings: list[Finding],
    history_series: dict[str, list[dict]],
    max_charts: int = 3,
) -> ChartResolution:
    result = ChartResolution()
    for request in requests[:max_charts]:
        try:
            chart = _resolve_one(request, ledger, findings, history_series)
        except _Unresolvable as exc:
            result.rejected.append((request, str(exc)))
            continue
        if chart is not None:
            result.charts.append(chart)
    return result


class _Unresolvable(Exception):
    pass


def _resolve_one(
    request: ChartRequest,
    ledger: EvidenceLedger,
    findings: list[Finding],
    history_series: dict[str, list[dict]],
) -> ChartData | None:
    if request.chart_type is ChartType.NONE:
        return None
    if request.chart_type is ChartType.DATA_TABLE:
        return _data_table(request, ledger)
    if request.chart_type is ChartType.BAR:
        return _bar(request, ledger)
    if request.chart_type is ChartType.PIE:
        return _composition(request, ledger, findings, ChartType.PIE)
    if request.chart_type is ChartType.DONUT:
        return _composition(request, ledger, findings, ChartType.DONUT)
    if request.chart_type is ChartType.SEVERITY_DISTRIBUTION:
        return _severity_distribution(request, findings)
    if request.chart_type is ChartType.TREND_LINE:
        return _trend_line(request, history_series)
    if request.chart_type is ChartType.STAT_TILE:
        return _stat_tile(request, ledger)
    raise _Unresolvable(f"unknown chart_type {request.chart_type}")


def _resolve_evidence(request: ChartRequest, ledger: EvidenceLedger) -> list:
    if not request.evidence_ids:
        raise _Unresolvable(f"{request.chart_type.value} requires evidence_ids")
    unknown = [eid for eid in request.evidence_ids if not ledger.has(eid)]
    if unknown:
        raise _Unresolvable(f"cites unknown evidence: {', '.join(unknown)}")
    return ledger.subset(request.evidence_ids)


def _data_table(request: ChartRequest, ledger: EvidenceLedger) -> ChartData:
    records = _resolve_evidence(request, ledger)
    by_device: dict[str, dict] = {}
    columns: list[str] = []
    for record in records:
        if not record.device_id:
            continue
        row = by_device.setdefault(
            record.device_id,
            # device_id is carried but not shown: the table displays the name,
            # while a click still has to act on the serial.
            {"device": record.device_label or record.device_id,
             "device_id": record.device_id},
        )
        row[record.field] = record.value
        if record.field not in columns:
            columns.append(record.field)
    if not by_device:
        raise _Unresolvable("no device-linked evidence to tabulate")
    return ChartData(
        chart_type=ChartType.DATA_TABLE,
        title=request.title,
        table_rows=list(by_device.values()),
        columns=["device", *columns],
        source_evidence_ids=[r.evidence_id for r in records],
    )


def _bar(request: ChartRequest, ledger: EvidenceLedger) -> ChartData:
    if not request.metric_field:
        raise _Unresolvable("bar chart requires metric_field")
    records = _resolve_evidence(request, ledger)
    matching = [r for r in records if r.field == request.metric_field]
    if not matching:
        raise _Unresolvable(
            f"none of the cited evidence has field '{request.metric_field}'"
        )
    unit = unit_for_metric(request.metric_field)
    points = [
        ChartPoint(
            label=r.device_label or r.device_id or r.evidence_id,
            device_id=r.device_id,
            value=r.value if isinstance(r.value, (int, float)) else str(r.value),
            unit=unit,
            evidence_id=r.evidence_id,
        )
        for r in matching
    ]
    points.sort(key=lambda p: p.value if isinstance(p.value, (int, float)) else 0)
    return ChartData(
        chart_type=ChartType.BAR,
        title=request.title,
        unit=unit,
        points=points,
        source_evidence_ids=[p.evidence_id for p in points if p.evidence_id],
    )


def _composition(
    request: ChartRequest,
    ledger: EvidenceLedger,
    findings: list[Finding],
    chart_type: ChartType,
) -> ChartData:
    """Pie or donut — shares of a whole.

    Three shapes, chosen by what the model referenced:

    * ``metric_field`` + ``evidence_ids`` → counts of each distinct value of
      that field among the cited evidence (categorical share).
    * ``finding_type`` set → severity mix for that detector.
    * otherwise → findings broken down by type.
    """
    kind = "donut" if chart_type is ChartType.DONUT else "pie"
    if request.metric_field:
        records = _resolve_evidence(request, ledger)
        matching = [r for r in records if r.field == request.metric_field]
        if not matching:
            raise _Unresolvable(
                f"none of the cited evidence has field '{request.metric_field}'"
            )
        counts: dict[str, int] = {}
        for record in matching:
            key = str(record.value)
            counts[key] = counts.get(key, 0) + 1
        points = [
            ChartPoint(label=label, value=count, unit=_COUNT_UNIT_DEVICES)
            for label, count in sorted(counts.items(), key=lambda kv: -kv[1])
        ]
        return ChartData(
            chart_type=chart_type,
            title=request.title,
            unit=_COUNT_UNIT_DEVICES,
            points=points,
            source_evidence_ids=[r.evidence_id for r in matching],
        )

    subset = findings
    if request.finding_type:
        try:
            wanted = FindingType(request.finding_type)
        except ValueError:
            raise _Unresolvable(f"unknown finding_type '{request.finding_type}'")
        subset = [f for f in findings if f.finding_type is wanted]
        if not subset:
            raise _Unresolvable(f"no findings of that type to summarise as a {kind}")
        counts = {}
        evidence_ids: list[str] = []
        for finding in subset:
            sev = finding.severity.value
            counts[sev] = counts.get(sev, 0) + 1
            evidence_ids.extend(finding.evidence_ids)
        points = [
            ChartPoint(
                label=sev,
                value=counts[sev],
                unit=_COUNT_UNIT_FINDINGS,
                severity=sev,
            )
            for sev in _SEVERITY_ORDER
            if sev in counts
        ]
        return ChartData(
            chart_type=chart_type,
            title=request.title,
            unit=_COUNT_UNIT_FINDINGS,
            points=points,
            source_evidence_ids=sorted(set(evidence_ids)),
        )

    if not findings:
        raise _Unresolvable(f"no findings to summarise as a {kind}")
    counts = {}
    evidence_ids = []
    for finding in findings:
        key = finding.finding_type.value
        counts[key] = counts.get(key, 0) + 1
        evidence_ids.extend(finding.evidence_ids)
    points = [
        ChartPoint(
            label=format_finding_type(label),
            value=count,
            unit=_COUNT_UNIT_FINDINGS,
        )
        for label, count in sorted(counts.items(), key=lambda kv: -kv[1])
    ]
    return ChartData(
        chart_type=chart_type,
        title=request.title,
        unit=_COUNT_UNIT_FINDINGS,
        points=points,
        source_evidence_ids=sorted(set(evidence_ids)),
    )


def _severity_distribution(request: ChartRequest, findings: list[Finding]) -> ChartData:
    subset = findings
    if request.finding_type:
        try:
            wanted = FindingType(request.finding_type)
        except ValueError:
            raise _Unresolvable(f"unknown finding_type '{request.finding_type}'")
        subset = [f for f in findings if f.finding_type is wanted]
    if not subset:
        raise _Unresolvable("no findings to summarise")

    counts: dict[str, int] = {}
    evidence_ids: list[str] = []
    for finding in subset:
        counts[finding.severity.value] = counts.get(finding.severity.value, 0) + 1
        evidence_ids.extend(finding.evidence_ids)

    points = [
        ChartPoint(
            label=sev,
            value=counts[sev],
            unit=_COUNT_UNIT_FINDINGS,
            severity=sev,
        )
        for sev in _SEVERITY_ORDER
        if sev in counts
    ]
    return ChartData(
        chart_type=ChartType.SEVERITY_DISTRIBUTION,
        title=request.title,
        unit=_COUNT_UNIT_FINDINGS,
        points=points,
        source_evidence_ids=sorted(set(evidence_ids)),
    )


def _trend_line(request: ChartRequest, history_series: dict[str, list[dict]]) -> ChartData:
    if not request.device_id or not request.metric_field:
        raise _Unresolvable("trend_line requires device_id and metric_field")
    key = f"{request.device_id}::{request.metric_field}"
    series = history_series.get(key)
    if not series:
        raise _Unresolvable(
            f"no history for {request.device_id}/{request.metric_field} was "
            "retrieved this turn — call get_device_history first"
        )
    unit = unit_for_metric(request.metric_field)
    points = [
        ChartPoint(label=p["collected_at"], value=p["value"], unit=unit)
        for p in series
        if isinstance(p.get("value"), (int, float))
    ]
    if not points:
        raise _Unresolvable("history series has no numeric readings to plot")
    return ChartData(
        chart_type=ChartType.TREND_LINE,
        title=request.title,
        unit=unit,
        points=points,
    )


def _stat_tile(request: ChartRequest, ledger: EvidenceLedger) -> ChartData:
    records = _resolve_evidence(request, ledger)
    if request.metric_field:
        matching = [r for r in records if r.field == request.metric_field]
        if not matching:
            raise _Unresolvable(f"no cited evidence has field '{request.metric_field}'")
        value = matching[0].value
        unit = unit_for_metric(request.metric_field)
        label = request.stat_label or unit
    else:
        value = len({r.device_id for r in records if r.device_id})
        unit = _COUNT_UNIT_DEVICES
        label = request.stat_label or unit
    return ChartData(
        chart_type=ChartType.STAT_TILE,
        title=request.title,
        unit=unit,
        stat_value=value,
        stat_label=label,
        source_evidence_ids=[r.evidence_id for r in records],
    )
