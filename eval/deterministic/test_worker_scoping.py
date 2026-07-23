"""Worker capability boundaries and dispatch invariants.

The manager/worker split only means anything if the boundaries are real, so
these assert the properties the architecture depends on — that ``action_agent``
genuinely cannot discover or cite on its own, that the discovery agents can, and
that a mis-dispatch is repaired before it reaches a worker.

No model is involved in any of it.
"""
from __future__ import annotations

import pytest

from fleet_copilot.agent.graph import build_graph
from fleet_copilot.agent.nodes.manager import normalize_dispatch
from fleet_copilot.agent.workers import (
    DISCOVERY_WORKERS,
    EVIDENCE_EMITTING_TOOLS,
    WORKER_REGISTRY,
    WorkerName,
    config_for,
)
from fleet_copilot.mcp_server.server import ACTION_TOOLS, READ_TOOLS

ALL_TOOL_NAMES = {t.__name__ for t in (*READ_TOOLS, *ACTION_TOOLS)}
ACTION_TOOL_NAMES = {t.__name__ for t in ACTION_TOOLS}


# ---------------------------------------------------------------------------
# Registry integrity
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("worker", list(WorkerName))
def test_every_allowed_tool_exists(worker):
    """A typo in the registry would silently shrink a worker's capability."""
    unknown = set(config_for(worker).allowed_tools) - ALL_TOOL_NAMES
    assert not unknown, f"{worker.value} lists non-existent tool(s): {unknown}"


def test_every_tool_is_reachable_by_some_worker():
    """A tool no worker holds is dead weight on the server."""
    covered = {name for cfg in WORKER_REGISTRY.values() for name in cfg.allowed_tools}
    assert ALL_TOOL_NAMES - covered == set()


def test_evidence_emitting_set_matches_the_server():
    """These five are the only tools whose results can be cited.

    Pinned rather than derived: adding a tool that emits evidence widens what an
    answer may rest on, and — because ``action_agent`` is defined by holding
    none of them — is also the way its inability to discover a device could be
    lost by accident. Changing this set should be a decision, not a side effect.
    """
    assert EVIDENCE_EMITTING_TOOLS <= ALL_TOOL_NAMES
    assert EVIDENCE_EMITTING_TOOLS == {
        "query_devices",
        "get_compliance_status",
        "get_device_history",
        "run_insight_scan",
        "run_read_query",
    }


# ---------------------------------------------------------------------------
# The capability boundaries the architecture rests on
# ---------------------------------------------------------------------------
def test_action_agent_cannot_produce_evidence():
    """The core invariant: it can only act on what it was handed.

    If action_agent ever gained an evidence-emitting tool it could justify its
    own proposals, and the manager's sequencing would stop being load-bearing.
    """
    cfg = config_for(WorkerName.ACTION)
    assert not (set(cfg.allowed_tools) & EVIDENCE_EMITTING_TOOLS)
    assert not cfg.emits_evidence


def test_action_agent_cannot_discover_devices():
    for tool in ("query_devices", "run_insight_scan", "get_compliance_status"):
        assert tool not in config_for(WorkerName.ACTION).allowed_tools


@pytest.mark.parametrize("worker", DISCOVERY_WORKERS)
def test_discovery_agents_can_produce_evidence(worker):
    """Otherwise they could not hand anything to the action agent."""
    assert config_for(worker).emits_evidence


def test_only_the_action_agent_holds_action_tools():
    for worker, cfg in WORKER_REGISTRY.items():
        overlap = set(cfg.allowed_tools) & ACTION_TOOL_NAMES
        if worker is WorkerName.ACTION:
            assert overlap == ACTION_TOOL_NAMES
        else:
            assert not overlap, f"{worker.value} can propose actions"


def test_only_the_insight_agent_can_run_the_detectors():
    for worker, cfg in WORKER_REGISTRY.items():
        has_scan = "run_insight_scan" in cfg.allowed_tools
        assert has_scan is (worker is WorkerName.INSIGHT)


def test_action_agent_has_the_smallest_iteration_budget():
    """Proposing is not exploratory; this bounds a two-worker turn's cost."""
    action = config_for(WorkerName.ACTION).max_iterations
    assert all(
        action <= config_for(w).max_iterations for w in DISCOVERY_WORKERS
    )


# ---------------------------------------------------------------------------
# Dispatch normalisation
# ---------------------------------------------------------------------------
def test_lone_action_agent_gets_a_discovery_agent_prepended():
    queue, repairs = normalize_dispatch([WorkerName.ACTION], intent="action")

    assert queue == [WorkerName.QA, WorkerName.ACTION]
    assert any("cannot discover" in r for r in repairs)


def test_lone_action_agent_on_an_insight_question_gets_the_insight_agent():
    queue, _ = normalize_dispatch([WorkerName.ACTION], intent="insight")
    assert queue == [WorkerName.INSIGHT, WorkerName.ACTION]


def test_action_agent_is_moved_last():
    queue, repairs = normalize_dispatch(
        [WorkerName.ACTION, WorkerName.INSIGHT], intent="action"
    )

    assert queue == [WorkerName.INSIGHT, WorkerName.ACTION]
    assert any("moved action_agent last" in r for r in repairs)


def test_duplicates_are_removed():
    queue, repairs = normalize_dispatch(
        [WorkerName.QA, WorkerName.QA], intent="qa"
    )

    assert queue == [WorkerName.QA]
    assert any("duplicate" in r for r in repairs)


def test_a_valid_dispatch_is_left_alone():
    queue, repairs = normalize_dispatch(
        [WorkerName.INSIGHT, WorkerName.ACTION], intent="action"
    )

    assert queue == [WorkerName.INSIGHT, WorkerName.ACTION]
    assert repairs == []


@pytest.mark.parametrize("intent", ["qa", "insight", "action"])
def test_normalisation_never_produces_an_unusable_queue(intent):
    """Whatever the manager returns, the queue must be runnable."""
    for candidate in (
        [WorkerName.ACTION],
        [WorkerName.ACTION, WorkerName.QA],
        [WorkerName.QA, WorkerName.ACTION],
        [WorkerName.INSIGHT],
    ):
        queue, _ = normalize_dispatch(list(candidate), intent=intent)
        assert 1 <= len(queue) <= 2
        assert len(set(queue)) == len(queue)
        if WorkerName.ACTION in queue:
            assert queue[-1] is WorkerName.ACTION
            assert config_for(queue[0]).emits_evidence


# ---------------------------------------------------------------------------
# Graph wiring
# ---------------------------------------------------------------------------
def test_worker_node_loops_until_the_queue_drains():
    edges = {(e.source, e.target) for e in build_graph().get_graph().edges}
    assert ("run_worker", "run_worker") in edges
    assert ("manager", "run_worker") in edges
    assert ("run_worker", "ground") in edges


def test_planning_still_precedes_dispatch():
    edges = {(e.source, e.target) for e in build_graph().get_graph().edges}
    assert ("plan", "manager") in edges
    assert ("plan", "refuse") in edges
