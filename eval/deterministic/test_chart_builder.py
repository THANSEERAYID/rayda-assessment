"""Chart resolution follows the same grounding rule as claims.

A chart request is a reference into what was actually retrieved this turn. Each
test here mirrors a way that reference can fail to resolve, and asserts the
request is dropped rather than rendered with a substituted or invented value.
"""
from __future__ import annotations

from datetime import datetime

from fleet_copilot.domain.charts import ChartRequest, ChartType
from fleet_copilot.domain.enums import FindingType, Severity
from fleet_copilot.domain.models import Finding
from fleet_copilot.evidence.chart_builder import resolve_charts
from fleet_copilot.evidence.ledger import EvidenceLedger, build_evidence


def _ledger() -> tuple[EvidenceLedger, str, str]:
    ledger = EvidenceLedger()
    ts = datetime(2026, 6, 12, 9, 0)
    a = build_evidence(
        tool="query_devices", field="disk_free_pct", value=2.0, device_id="DEV-1", snapshot_ts=ts
    )
    b = build_evidence(
        tool="query_devices", field="disk_free_pct", value=23.8, device_id="DEV-2", snapshot_ts=ts
    )
    ledger.add(a)
    ledger.add(b)
    return ledger, a.evidence_id, b.evidence_id


def _finding(device_id: str, severity: Severity, finding_type=FindingType.DISK_PRESSURE) -> Finding:
    return Finding(
        finding_type=finding_type,
        device_id=device_id,
        company_id="acme-001",
        severity=severity,
        title=f"{finding_type.value} on {device_id}",
        metrics={},
        evidence_ids=["ev-1"],
    )


def test_bar_chart_resolves_from_cited_evidence():
    ledger, a, b = _ledger()
    request = ChartRequest(
        chart_type=ChartType.BAR,
        title="Disk free % by device",
        metric_field="disk_free_pct",
        evidence_ids=[a, b],
    )
    result = resolve_charts([request], ledger=ledger, findings=[], history_series={})

    assert not result.rejected
    chart = result.charts[0]
    assert {p.label for p in chart.points} == {"DEV-1", "DEV-2"}
    assert sorted(p.value for p in chart.points) == [2.0, 23.8]


def test_bar_chart_rejects_fabricated_evidence_id():
    ledger, _, _ = _ledger()
    request = ChartRequest(
        chart_type=ChartType.BAR,
        title="Fake",
        metric_field="disk_free_pct",
        evidence_ids=["ev-doesnotexist"],
    )
    result = resolve_charts([request], ledger=ledger, findings=[], history_series={})

    assert not result.charts
    assert "unknown evidence" in result.rejected[0][1]


def test_bar_chart_rejects_a_metric_field_absent_from_the_cited_evidence():
    """The evidence is real, but doesn't describe the field asked for."""
    ledger, a, _ = _ledger()
    request = ChartRequest(
        chart_type=ChartType.BAR,
        title="Wrong field",
        metric_field="ram_used_pct",
        evidence_ids=[a],
    )
    result = resolve_charts([request], ledger=ledger, findings=[], history_series={})

    assert not result.charts
    assert "disk_free_pct" not in result.rejected[0][1] or "none of" in result.rejected[0][1]


def test_data_table_groups_by_device():
    ledger, a, b = _ledger()
    request = ChartRequest(
        chart_type=ChartType.DATA_TABLE, title="Fleet detail", evidence_ids=[a, b]
    )
    result = resolve_charts([request], ledger=ledger, findings=[], history_series={})

    chart = result.charts[0]
    assert len(chart.table_rows) == 2
    assert "disk_free_pct" in chart.columns
    # The table shows the readable name, not the serial...
    assert "device" in chart.columns
    assert "device_id" not in chart.columns
    # ...but carries the serial so a click still acts on something the tools accept.
    assert all("device_id" in row for row in chart.table_rows)


def test_severity_distribution_counts_by_severity():
    findings = [
        _finding("DEV-1", Severity.HIGH),
        _finding("DEV-2", Severity.HIGH),
        _finding("DEV-3", Severity.MEDIUM),
    ]
    request = ChartRequest(
        chart_type=ChartType.SEVERITY_DISTRIBUTION, title="By severity"
    )
    result = resolve_charts([request], ledger=EvidenceLedger(), findings=findings, history_series={})

    chart = result.charts[0]
    values = {p.label: p.value for p in chart.points}
    assert values == {"high": 2, "medium": 1}


def test_severity_distribution_filters_by_finding_type():
    findings = [
        _finding("DEV-1", Severity.HIGH, FindingType.DISK_PRESSURE),
        _finding("DEV-2", Severity.HIGH, FindingType.RAM_PRESSURE),
    ]
    request = ChartRequest(
        chart_type=ChartType.SEVERITY_DISTRIBUTION,
        title="Disk only",
        finding_type="disk_pressure",
    )
    result = resolve_charts([request], ledger=EvidenceLedger(), findings=findings, history_series={})

    chart = result.charts[0]
    assert sum(p.value for p in chart.points) == 1


def test_severity_distribution_rejects_unknown_finding_type():
    request = ChartRequest(
        chart_type=ChartType.SEVERITY_DISTRIBUTION,
        title="Bad",
        finding_type="not_a_real_type",
    )
    result = resolve_charts(
        [request], ledger=EvidenceLedger(), findings=[_finding("DEV-1", Severity.HIGH)], history_series={}
    )
    assert not result.charts
    assert "unknown finding_type" in result.rejected[0][1]


def test_trend_line_resolves_from_a_series_actually_retrieved_this_turn():
    series = {
        "DEV-1::disk_free_pct": [
            {"collected_at": "2026-06-01T00:00:00", "value": 40.0},
            {"collected_at": "2026-06-02T00:00:00", "value": 38.0},
        ]
    }
    request = ChartRequest(
        chart_type=ChartType.TREND_LINE,
        title="Disk trend",
        device_id="DEV-1",
        metric_field="disk_free_pct",
    )
    result = resolve_charts([request], ledger=EvidenceLedger(), findings=[], history_series=series)

    assert not result.rejected
    assert len(result.charts[0].points) == 2


def test_trend_line_is_rejected_when_history_was_never_fetched():
    """The device is real, but its history wasn't retrieved this turn."""
    request = ChartRequest(
        chart_type=ChartType.TREND_LINE,
        title="Disk trend",
        device_id="DEV-9",
        metric_field="disk_free_pct",
    )
    result = resolve_charts([request], ledger=EvidenceLedger(), findings=[], history_series={})

    assert not result.charts
    assert "get_device_history" in result.rejected[0][1]


def test_stat_tile_counts_distinct_devices_when_no_metric_given():
    ledger, a, b = _ledger()
    request = ChartRequest(
        chart_type=ChartType.STAT_TILE, title="Devices affected", evidence_ids=[a, b]
    )
    result = resolve_charts([request], ledger=ledger, findings=[], history_series={})

    assert result.charts[0].stat_value == 2


def test_stat_tile_reports_a_specific_field_when_requested():
    ledger, a, _ = _ledger()
    request = ChartRequest(
        chart_type=ChartType.STAT_TILE,
        title="Lowest disk",
        metric_field="disk_free_pct",
        evidence_ids=[a],
    )
    result = resolve_charts([request], ledger=ledger, findings=[], history_series={})

    assert result.charts[0].stat_value == 2.0


def test_none_chart_type_produces_no_chart():
    request = ChartRequest(chart_type=ChartType.NONE, title="n/a")
    result = resolve_charts([request], ledger=EvidenceLedger(), findings=[], history_series={})

    assert not result.charts
    assert not result.rejected


def test_requests_are_capped_at_max_charts():
    ledger, a, b = _ledger()
    requests = [
        ChartRequest(
            chart_type=ChartType.STAT_TILE, title=f"Tile {i}", evidence_ids=[a, b]
        )
        for i in range(5)
    ]
    result = resolve_charts(requests, ledger=ledger, findings=[], history_series={}, max_charts=3)
    assert len(result.charts) == 3


def test_a_bar_point_shows_the_name_and_carries_the_serial():
    """Serials are unreadable; the chart shows the name but clicks need the id."""
    ledger = EvidenceLedger()
    record = build_evidence(
        tool="query_devices",
        field="disk_free_pct",
        value=2.0,
        device_id="MT7PJB7N5LRE",
        device_label="acme-macbook-4 (MacBook Pro)",
    )
    ledger.add(record)
    request = ChartRequest(
        chart_type=ChartType.BAR,
        title="Disk free",
        metric_field="disk_free_pct",
        evidence_ids=[record.evidence_id],
    )
    point = resolve_charts(
        [request], ledger=ledger, findings=[], history_series={}
    ).charts[0].points[0]

    assert point.label == "acme-macbook-4 (MacBook Pro)"
    assert point.device_id == "MT7PJB7N5LRE"


def test_a_point_falls_back_to_the_serial_when_unlabelled():
    ledger = EvidenceLedger()
    record = build_evidence(
        tool="query_devices", field="disk_free_pct", value=2.0, device_id="DEV-1"
    )
    ledger.add(record)
    request = ChartRequest(
        chart_type=ChartType.BAR,
        title="Disk free",
        metric_field="disk_free_pct",
        evidence_ids=[record.evidence_id],
    )
    point = resolve_charts(
        [request], ledger=ledger, findings=[], history_series={}
    ).charts[0].points[0]

    assert point.label == "DEV-1"


def test_pie_chart_resolves_findings_by_type():
    findings = [
        _finding("DEV-1", Severity.HIGH, FindingType.DISK_PRESSURE),
        _finding("DEV-2", Severity.MEDIUM, FindingType.DISK_PRESSURE),
        _finding("DEV-3", Severity.HIGH, FindingType.BATTERY_EOL),
    ]
    request = ChartRequest(chart_type=ChartType.PIE, title="Findings by type")
    result = resolve_charts(
        [request], ledger=EvidenceLedger(), findings=findings, history_series={}
    )

    assert not result.rejected
    chart = result.charts[0]
    assert chart.chart_type is ChartType.PIE
    by_label = {p.label: p.value for p in chart.points}
    assert by_label["Disk pressure"] == 2
    assert by_label["Battery end of life"] == 1


def test_pie_chart_resolves_severity_when_finding_type_set():
    findings = [
        _finding("DEV-1", Severity.HIGH, FindingType.DISK_PRESSURE),
        _finding("DEV-2", Severity.LOW, FindingType.DISK_PRESSURE),
        _finding("DEV-3", Severity.HIGH, FindingType.BATTERY_EOL),
    ]
    request = ChartRequest(
        chart_type=ChartType.PIE,
        title="Disk pressure severity",
        finding_type="disk_pressure",
    )
    result = resolve_charts(
        [request], ledger=EvidenceLedger(), findings=findings, history_series={}
    )

    assert not result.rejected
    by_label = {p.label: p.value for p in result.charts[0].points}
    assert by_label == {"high": 1, "low": 1}


def test_pie_chart_resolves_categorical_evidence_shares():
    ledger = EvidenceLedger()
    ts = datetime(2026, 6, 12, 9, 0)
    for device, status in [("DEV-1", "fail"), ("DEV-2", "fail"), ("DEV-3", "pass")]:
        ledger.add(
            build_evidence(
                tool="get_compliance_status",
                field="compliance.screen_lock",
                value=status,
                device_id=device,
                snapshot_ts=ts,
            )
        )
    request = ChartRequest(
        chart_type=ChartType.PIE,
        title="Screen lock status",
        metric_field="compliance.screen_lock",
        evidence_ids=list(ledger.ids()),
    )
    result = resolve_charts(
        [request], ledger=ledger, findings=[], history_series={}
    )

    assert not result.rejected
    by_label = {p.label: p.value for p in result.charts[0].points}
    assert by_label == {"fail": 2, "pass": 1}


def test_donut_chart_resolves_findings_by_type():
    findings = [
        _finding("DEV-1", Severity.HIGH, FindingType.DISK_PRESSURE),
        _finding("DEV-2", Severity.MEDIUM, FindingType.DISK_PRESSURE),
        _finding("DEV-3", Severity.HIGH, FindingType.BATTERY_EOL),
    ]
    request = ChartRequest(chart_type=ChartType.DONUT, title="Findings by type")
    result = resolve_charts(
        [request], ledger=EvidenceLedger(), findings=findings, history_series={}
    )

    assert not result.rejected
    chart = result.charts[0]
    assert chart.chart_type is ChartType.DONUT
    assert sum(int(p.value) for p in chart.points) == len(findings)
