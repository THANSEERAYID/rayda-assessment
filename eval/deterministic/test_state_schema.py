"""Graph state is a validated schema, not a loose dict.

Two directions matter, and LangGraph only helps with one of them:

* **reads** are protected by attribute access on the Pydantic model — a typo
  raises instead of silently yielding ``None``;
* **writes** are not protected by LangGraph at all. It applies a node's update
  dict without validating it against the state schema, so an unknown key is
  silently discarded. ``validated_node`` is what closes that, and these tests
  are what keep it closed.
"""
from __future__ import annotations

import asyncio

import pytest

from fleet_copilot.agent.nodes._common import validated_node
from fleet_copilot.agent.state import STATE_FIELDS, AgentState, new_state


def test_new_state_populates_the_bound_fields():
    state = new_state(
        thread_id="thr-1", turn_id="turn-1", company_id="acme-001", question="hi"
    )

    assert state.thread_id == "thr-1"
    assert state.company_id == "acme-001"
    assert state.question == "hi"


def test_unset_fields_take_their_declared_defaults():
    state = new_state(thread_id="t", turn_id="u", company_id="c", question="q")

    assert state.evidence == {}
    assert state.findings == []
    assert state.dispatch_queue == []
    assert state.dispatch_index == 0
    assert state.awaiting_approval is False
    assert state.step_seq == 0


def test_reading_a_misspelled_field_raises():
    """The whole point of attribute access over ``.get()``."""
    state = new_state(thread_id="t", turn_id="u", company_id="c", question="q")

    with pytest.raises(AttributeError):
        _ = state.evidnce


def test_constructing_with_an_unknown_field_is_rejected():
    with pytest.raises(Exception):
        AgentState(question="q", not_a_real_field=1)


def test_state_fields_matches_the_model():
    assert STATE_FIELDS == frozenset(AgentState.model_fields)
    assert "evidence" in STATE_FIELDS
    assert "dispatch_queue" in STATE_FIELDS


# ---------------------------------------------------------------------------
# The write path — what LangGraph does not do for us
# ---------------------------------------------------------------------------
def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_validated_node_passes_a_correct_update_through():
    @validated_node
    async def node(state, config):
        return {"answer": "ok", "step_seq": 3}

    state = new_state(thread_id="t", turn_id="u", company_id="c", question="q")
    assert _run(node(state, {})) == {"answer": "ok", "step_seq": 3}


def test_validated_node_rejects_a_misspelled_field():
    """Without this, LangGraph would silently drop the key."""

    @validated_node
    async def node(state, config):
        return {"answr": "oops"}

    state = new_state(thread_id="t", turn_id="u", company_id="c", question="q")
    with pytest.raises(ValueError, match="unknown state field"):
        _run(node(state, {}))


def test_validated_node_names_the_offending_field_and_the_node():
    @validated_node
    async def some_node(state, config):
        return {"totally_wrong": 1}

    state = new_state(thread_id="t", turn_id="u", company_id="c", question="q")
    with pytest.raises(ValueError) as exc:
        _run(some_node(state, {}))

    assert "some_node" in str(exc.value)
    assert "totally_wrong" in str(exc.value)


def test_validated_node_tolerates_an_empty_update():
    @validated_node
    async def node(state, config):
        return {}

    state = new_state(thread_id="t", turn_id="u", company_id="c", question="q")
    assert _run(node(state, {})) == {}


def test_every_graph_node_is_validated():
    """A new node added without the decorator would silently lose typo'd writes."""
    from fleet_copilot.agent.nodes.approval import approval_node, execute_action_node
    from fleet_copilot.agent.nodes.ground import ground_node
    from fleet_copilot.agent.nodes.manager import manager_node
    from fleet_copilot.agent.nodes.plan import plan_node
    from fleet_copilot.agent.nodes.respond import refuse_node, respond_node
    from fleet_copilot.agent.nodes.worker import worker_node

    nodes = [
        plan_node,
        manager_node,
        worker_node,
        ground_node,
        approval_node,
        execute_action_node,
        respond_node,
        refuse_node,
    ]
    for node in nodes:
        assert getattr(node, "__wrapped__", None) is not None, (
            f"{node.__name__} is not wrapped in @validated_node"
        )
