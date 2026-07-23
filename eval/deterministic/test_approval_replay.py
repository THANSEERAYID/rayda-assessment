"""One human decision must leave exactly one record.

LangGraph resumes an interrupted node by re-running it from the top, so
everything above ``interrupt()`` executes twice for a single pause. On the
approval gate that is the one step a person is accountable for, and a duplicate
row makes the audit trail overstate how many times approval was sought.

No model calls here — this exercises the trace repository and the replay guard
directly.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from fleet_copilot.agent.nodes import _common
from fleet_copilot.agent.nodes._common import step_already_recorded
from fleet_copilot.storage.repositories.audit import RunTraceRepository


class _State:
    """The fields the recorders read, without building a whole AgentState."""

    def __init__(self, thread_id: str, turn_id: str, step_seq: int = 11) -> None:
        self.thread_id = thread_id
        self.turn_id = turn_id
        self.step_seq = step_seq


def _record(conn, state: _State, node: str, seq: int) -> None:
    RunTraceRepository(conn).record_step(
        thread_id=state.thread_id,
        turn_id=state.turn_id,
        seq=seq,
        node=node,
        status="waiting",
        detail={"count": 3},
    )


def test_has_step_is_false_before_anything_is_recorded(conn):
    assert not RunTraceRepository(conn).has_step(
        thread_id="thr-replay", turn_id="turn-replay", node="human_approval", seq=12
    )


def test_has_step_finds_the_exact_step(conn):
    state = _State("thr-replay-1", "turn-replay-1")
    _record(conn, state, "human_approval", 12)
    repo = RunTraceRepository(conn)
    assert repo.has_step(
        thread_id=state.thread_id, turn_id=state.turn_id, node="human_approval", seq=12
    )


def test_has_step_is_scoped_to_node_seq_and_turn(conn):
    """A near-miss on any part of the signature must not read as a replay."""
    state = _State("thr-replay-2", "turn-replay-2")
    _record(conn, state, "human_approval", 12)
    repo = RunTraceRepository(conn)

    assert not repo.has_step(  # different node at the same position
        thread_id=state.thread_id, turn_id=state.turn_id, node="execute_action", seq=12
    )
    assert not repo.has_step(  # same node later in the turn
        thread_id=state.thread_id, turn_id=state.turn_id, node="human_approval", seq=14
    )
    assert not repo.has_step(  # same node and seq in another turn
        thread_id=state.thread_id, turn_id="turn-other", node="human_approval", seq=12
    )


@pytest.fixture()
def guard(conn, monkeypatch):
    """``step_already_recorded`` bound to the test transaction.

    The helper opens its own connection, which cannot see writes the fixture
    has not committed — so point it at the same one for the duration.
    """

    @contextmanager
    def _connect():
        yield conn

    monkeypatch.setattr(_common, "connect", _connect)
    return step_already_recorded


def test_replay_of_the_same_pause_is_detected(conn, guard):
    """The signature the guard relies on: a replay recomputes the same seq."""
    state = _State("thr-replay-3", "turn-replay-3", step_seq=11)
    assert not guard(state, "human_approval")

    _record(conn, state, "human_approval", 12)
    # The node re-runs from the same checkpointed state, so step_seq is
    # unchanged and the guard now reports the step as already written.
    assert guard(state, "human_approval")


def test_a_second_approval_round_is_not_treated_as_a_replay(conn, guard):
    """Partial approval pauses again — a real pause, and it must be recorded.

    After execute_action the sequence has moved on, so the next approval step
    lands at a different seq and the guard correctly lets it through.
    """
    state = _State("thr-replay-4", "turn-replay-4", step_seq=11)
    _record(conn, state, "human_approval", 12)
    _record(conn, state, "execute_action", 13)

    resumed = _State("thr-replay-4", "turn-replay-4", step_seq=13)
    assert not guard(resumed, "human_approval")
