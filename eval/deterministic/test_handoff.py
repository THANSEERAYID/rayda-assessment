"""The handoff between workers.

``action_agent`` holds no evidence-emitting tool, so the only way it can cite
anything is if the previous worker's evidence reaches its prompt. If this
regresses, every action proposal starts failing for insufficient evidence and
the two-agent dispatch becomes pointless — so it is asserted directly rather
than left to the live tier to discover.
"""
from __future__ import annotations

from fleet_copilot.agent.nodes._common import render_handoff
from fleet_copilot.agent.state import AgentState


def _state(**overrides) -> AgentState:
    return AgentState(question="Flag the worst battery.", **overrides)


def test_first_worker_receives_no_handoff():
    assert render_handoff(_state()) is None


def test_evidence_ids_reach_the_next_worker():
    state = _state(
        evidence={
            "ev-abc123": {
                "evidence_id": "ev-abc123",
                "device_id": "8NM23J95R5I6",
                "field": "battery.cycle_count",
                "value": 1157,
                "snapshot_ts": "2026-06-12T09:00:00",
            }
        }
    )
    handoff = render_handoff(state)

    assert handoff is not None
    assert "ev-abc123" in handoff
    assert "8NM23J95R5I6" in handoff
    assert "1157" in handoff


def test_findings_carry_their_evidence_ids():
    state = _state(
        findings=[
            {
                "finding_type": "battery_eol",
                "device_id": "8NM23J95R5I6",
                "severity": "high",
                "title": "Battery approaching end of life",
                "metrics": {"cycle_count": 1157},
                "evidence_ids": ["ev-abc123", "ev-def456"],
            }
        ]
    )
    handoff = render_handoff(state)

    assert "battery_eol" in handoff
    assert "ev-abc123" in handoff
    assert "ev-def456" in handoff


def test_existing_proposals_are_disclosed():
    """So a second action does not duplicate one already awaiting approval."""
    state = _state(
        proposals=[
            {
                "action_id": "act-123",
                "action_type": "flag_device_for_replacement",
                "target_device_id": "8NM23J95R5I6",
            }
        ]
    )
    handoff = render_handoff(state)

    assert "act-123" in handoff
    assert "flag_device_for_replacement" in handoff


def test_large_ledgers_are_truncated_but_flagged():
    evidence = {
        f"ev-{i:06d}": {
            "evidence_id": f"ev-{i:06d}",
            "device_id": f"DEV-{i}",
            "field": "disk_free_pct",
            "value": i,
            "snapshot_ts": "2026-06-12T09:00:00",
        }
        for i in range(150)
    }
    handoff = render_handoff(_state(evidence=evidence), limit=120)

    assert "150 records" in handoff
    assert "plus 30 more records" in handoff
