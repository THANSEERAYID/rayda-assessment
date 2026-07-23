"""Tool payloads are validated where they enter the agent.

State stores evidence, findings and proposals as plain dicts so the checkpointer
can serialise them — but they are parsed into domain models on the way in. The
point is attribution: a malformed payload fails at the tool call that produced
it, rather than surfacing several nodes later as ``None`` in a handoff or an
exception inside the grounding node.
"""
from __future__ import annotations

import pytest

from fleet_copilot.agent.nodes.worker import (
    MalformedToolResult,
    _ingest,
    _parse,
    _validated,
)
from fleet_copilot.domain.models import Evidence, Finding

VALID_EVIDENCE = {
    "evidence_id": "ev-1",
    "tool": "query_devices",
    "device_id": "8NM23J95R5I6",
    "field": "disk_free_pct",
    "value": 2.6,
    "snapshot_ts": "2026-06-12T09:00:00",
    "detail": {},
}

VALID_FINDING = {
    "finding_type": "disk_pressure",
    "device_id": "8NM23J95R5I6",
    "company_id": "acme-001",
    "severity": "high",
    "title": "Storage pressure",
    "metrics": {"current_free_pct": 2.6},
    "evidence_ids": ["ev-1"],
    "explanation": None,
}

VALID_ACTION = {
    "action_id": "act-1",
    "thread_id": "thr-1",
    "company_id": "acme-001",
    "action_type": "open_remediation_ticket",
    "target_device_id": "8NM23J95R5I6",
    "target_employee_id": None,
    "params": {"check_id": "disk_space", "note": "clear space"},
    "justification": "2.6% free",
    "evidence_ids": ["ev-1"],
    "status": "proposed",
    "created_at": None,
    "decided_at": None,
    "decided_by": None,
    "result": None,
}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_valid_evidence_becomes_a_model():
    result = _ingest({"evidence": [VALID_EVIDENCE]}, tool="query_devices")

    assert len(result.evidence) == 1
    assert isinstance(result.evidence[0], Evidence)
    assert result.evidence[0].evidence_id == "ev-1"


def test_valid_findings_become_models():
    result = _ingest({"findings": [VALID_FINDING]}, tool="run_insight_scan")

    assert isinstance(result.findings[0], Finding)
    assert result.findings[0].device_id == "8NM23J95R5I6"


def test_an_action_is_only_ingested_from_an_action_tool():
    payload = {"action": VALID_ACTION}

    assert _ingest(payload, tool="open_remediation_ticket").proposals
    # A read tool returning an "action" key is not a proposal.
    assert not _ingest(payload, tool="query_devices").proposals


def test_an_empty_result_ingests_cleanly():
    result = _ingest({}, tool="query_devices")

    assert result.evidence == []
    assert result.findings == []
    assert result.proposals == []


def test_ingested_models_round_trip_back_to_json():
    """State must stay serialisable for the checkpointer."""
    import json

    result = _ingest({"evidence": [VALID_EVIDENCE]}, tool="query_devices")
    dumped = result.evidence[0].model_dump(mode="json")

    json.dumps(dumped)  # must not raise
    assert dumped["evidence_id"] == "ev-1"


# ---------------------------------------------------------------------------
# Malformed payloads fail at the source
# ---------------------------------------------------------------------------
def test_evidence_missing_its_id_is_rejected():
    bad = {k: v for k, v in VALID_EVIDENCE.items() if k != "evidence_id"}

    with pytest.raises(MalformedToolResult, match="evidence_id"):
        _ingest({"evidence": [bad]}, tool="query_devices")


def test_the_error_names_the_offending_index_and_field():
    """So a bad record in a large payload can actually be found."""
    bad = {k: v for k, v in VALID_EVIDENCE.items() if k != "field"}

    with pytest.raises(MalformedToolResult) as exc:
        _ingest({"evidence": [VALID_EVIDENCE, bad]}, tool="query_devices")

    assert "evidence[1]" in str(exc.value)
    assert "field" in str(exc.value)


def test_a_finding_with_an_invalid_severity_is_rejected():
    bad = {**VALID_FINDING, "severity": "catastrophic"}

    with pytest.raises(MalformedToolResult, match="severity"):
        _ingest({"findings": [bad]}, tool="run_insight_scan")


def test_a_finding_with_an_unknown_type_is_rejected():
    bad = {**VALID_FINDING, "finding_type": "not_a_detector"}

    with pytest.raises(MalformedToolResult, match="finding_type"):
        _ingest({"findings": [bad]}, tool="run_insight_scan")


def test_a_proposal_with_an_invalid_status_is_rejected():
    """Nothing may enter state already claiming to be executed."""
    bad = {**VALID_ACTION, "status": "totally_done"}

    with pytest.raises(MalformedToolResult, match="status"):
        _ingest({"action": bad}, tool="open_remediation_ticket")


def test_validated_reports_the_kind_it_was_parsing():
    with pytest.raises(MalformedToolResult, match="findings"):
        _validated(Finding, [{"nope": 1}], "findings")


# ---------------------------------------------------------------------------
# Transport parsing
# ---------------------------------------------------------------------------
def test_parse_handles_json_text_from_mcp():
    assert _parse('{"match_count": 3}') == {"match_count": 3}


def test_parse_handles_a_dict_unchanged():
    assert _parse({"match_count": 3}) == {"match_count": 3}


def test_parse_wraps_non_json_text():
    assert _parse("not json") == {"result": "not json"}


def test_parse_unwraps_the_adapter_content_block():
    """The shape the LangChain MCP adapter actually returns.

    Regression: this was previously returning the *wrapper* — a useless
    {"type", "text"} dict — so no tool payload ever reached the agent and every
    turn refused for lack of evidence. Direct call_tool() tests missed it
    because they read .content[0].text themselves.
    """
    block = [{"type": "text", "text": '{"match_count": 2, "evidence": []}'}]
    assert _parse(block) == {"match_count": 2, "evidence": []}


def test_parse_unwraps_a_text_content_object():
    """The raw MCP client returns TextContent objects, not dicts."""

    class TextContent:
        text = '{"match_count": 1}'

    assert _parse([TextContent()]) == {"match_count": 1}


def test_parse_handles_an_empty_content_list():
    assert _parse([]) == {}


def test_history_points_are_validated():
    """A trend chart reads this series back much later, by key."""
    result = _ingest(
        {"points": [{"collected_at": "2026-06-12T09:00:00", "value": 2.6}]},
        tool="get_device_history",
    )
    assert len(result.points) == 1
    assert result.points[0].value == 2.6


def test_a_history_point_missing_its_timestamp_is_rejected():
    with pytest.raises(MalformedToolResult, match="points"):
        _ingest({"points": [{"value": 2.6}]}, tool="get_device_history")
