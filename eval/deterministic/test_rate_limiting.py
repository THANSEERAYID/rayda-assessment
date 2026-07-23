"""Rate limiting and loop-breaking bounds.

Three separate protections, each failing differently:

* the token bucket paces throughput so a multi-call turn does not trip a 429;
* the semaphore bounds how many calls are in flight at once, which the bucket
  alone does not do;
* the per-turn budget and the worker circuit breakers bound how much a single
  turn can spend before something stops it.

None of these call a model.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from fleet_copilot.agent.llm import LLMBudgetExceeded, check_budget, invoke_llm
from fleet_copilot.agent.rate_limit import get_rate_limiter, llm_slot, reset_limiters
from fleet_copilot.agent.state import AgentState
from fleet_copilot.config import settings


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        reset_limiters()  # the semaphore binds to the loop that creates it
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        reset_limiters()


class _FakeModel:
    """Stands in for a bound ChatOpenAI, recording when it was called."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.calls = 0
        self.concurrent = 0
        self.peak_concurrent = 0

    async def ainvoke(self, _messages, **_kwargs):
        self.calls += 1
        self.concurrent += 1
        self.peak_concurrent = max(self.peak_concurrent, self.concurrent)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            return "ok"
        finally:
            self.concurrent -= 1


# ---------------------------------------------------------------------------
# Per-turn budget
# ---------------------------------------------------------------------------
def test_budget_allows_calls_below_the_ceiling():
    check_budget(0)
    check_budget(settings.max_llm_calls_per_turn - 1)


def test_budget_stops_the_turn_at_the_ceiling():
    with pytest.raises(LLMBudgetExceeded):
        check_budget(settings.max_llm_calls_per_turn)


def test_budget_message_names_the_limit():
    with pytest.raises(LLMBudgetExceeded, match=str(settings.max_llm_calls_per_turn)):
        check_budget(settings.max_llm_calls_per_turn + 5)


def test_invoke_refuses_once_the_budget_is_spent():
    model = _FakeModel()

    async def scenario():
        await invoke_llm(model, [], calls_so_far=0)
        with pytest.raises(LLMBudgetExceeded):
            await invoke_llm(
                model, [], calls_so_far=settings.max_llm_calls_per_turn
            )

    _run(scenario())
    # The refused call must not have reached the provider.
    assert model.calls == 1


def test_state_tracks_calls_across_the_turn():
    """The counter is what makes the ceiling span every node, not just one."""
    state = AgentState(question="q", llm_calls=3)
    assert state.llm_calls == 3


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------
def test_concurrent_calls_are_capped():
    model = _FakeModel(delay=0.05)

    async def scenario():
        await asyncio.gather(
            *(invoke_llm(model, [], calls_so_far=0) for _ in range(12))
        )

    _run(scenario())

    assert model.calls == 12
    assert model.peak_concurrent <= settings.llm_max_concurrency


def test_the_slot_is_released_even_when_a_call_fails():
    """A raising call must not leak a permit and deadlock later turns."""

    class Failing:
        async def ainvoke(self, _messages, **_kwargs):
            raise RuntimeError("provider exploded")

    async def scenario():
        for _ in range(settings.llm_max_concurrency + 2):
            with pytest.raises(RuntimeError):
                await invoke_llm(Failing(), [], calls_so_far=0)
        # Still acquirable afterwards.
        async with llm_slot():
            return True

    assert _run(scenario()) is True


# ---------------------------------------------------------------------------
# Throughput
# ---------------------------------------------------------------------------
def test_the_token_bucket_paces_sustained_calls():
    """Beyond the burst allowance, calls must wait rather than flood."""

    async def scenario():
        limiter = get_rate_limiter()
        start = time.monotonic()
        # Burst size plus a few more; the extras have to wait for a refill.
        for _ in range(settings.llm_max_bucket_size + 3):
            await limiter.aacquire()
        return time.monotonic() - start

    elapsed = _run(scenario())
    # Three refills at the configured rate, allowing generous slack for CI.
    minimum = 3 / settings.llm_requests_per_second * 0.5
    assert elapsed >= minimum, f"no pacing observed ({elapsed:.2f}s)"


def test_the_limiter_is_shared_across_model_instances():
    """One bucket for the process, not one per node that builds a model."""
    reset_limiters()
    assert get_rate_limiter() is get_rate_limiter()


# ---------------------------------------------------------------------------
# Loop-breaking configuration
# ---------------------------------------------------------------------------
def test_loop_bounds_are_configured_coherently():
    # A single worker must not be able to exhaust the turn budget alone.
    assert settings.max_tool_iterations < settings.max_llm_calls_per_turn
    # Breakers must be able to fire before a loop runs to its own limit.
    assert settings.max_consecutive_tool_errors <= settings.max_tool_iterations
    assert settings.max_unproductive_iterations <= settings.max_tool_iterations


def test_the_worst_case_turn_fits_inside_the_budget():
    """Plan + manager + two workers + grounding retries must all fit."""
    from fleet_copilot.agent.workers import WORKER_REGISTRY, WorkerName

    worst = (
        1  # plan
        + 1  # manager
        + WORKER_REGISTRY[WorkerName.INSIGHT].max_iterations
        + WORKER_REGISTRY[WorkerName.ACTION].max_iterations
        + (settings.max_grounding_retries + 1)
    )
    assert worst <= settings.max_llm_calls_per_turn, (
        f"a legitimate turn needs {worst} calls but the budget is "
        f"{settings.max_llm_calls_per_turn}"
    )


# ---------------------------------------------------------------------------
# The worker circuit breakers, driven end to end
# ---------------------------------------------------------------------------
class _StubSession:
    """A tool session whose calls always return the same canned result."""

    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls = 0

    def tools_for(self, _names):
        return []

    async def call(self, _name, _args):
        import json

        self.calls += 1
        return json.dumps(self.result)


class _LoopingModel:
    """Always asks for more tool calls, with distinct args each time."""

    def __init__(self, calls_per_turn: int = 1) -> None:
        self.calls = 0
        self.calls_per_turn = calls_per_turn

    def bind_tools(self, _tools):
        return self

    async def ainvoke(self, _messages, **_kwargs):
        from langchain_core.messages import AIMessage

        self.calls += 1
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "query_devices",
                    "args": {"attempt": self.calls, "n": n},
                    "id": f"call-{self.calls}-{n}",
                }
                for n in range(self.calls_per_turn)
            ],
        )


def _drive_worker(monkeypatch, tool_result: dict, calls_per_turn: int = 1):
    from fleet_copilot.agent.nodes import worker as worker_module

    model = _LoopingModel(calls_per_turn)
    session = _StubSession(tool_result)
    monkeypatch.setattr(worker_module, "get_llm", lambda *a, **k: model)

    state = AgentState(
        thread_id="t", turn_id="u", company_id="acme-001", question="q",
        dispatch_queue=["qa_agent"], dispatch_index=0,
    )
    config = {"configurable": {"tool_session": session}}
    update = _run(worker_module.worker_node(state, config))
    return model, session, update


def test_failing_calls_break_the_loop_early(monkeypatch):
    """Every call failing means the route is not working — stop, don't spend.

    With one call per round, a failure is also a round with no progress, so the
    no-progress breaker reaches its threshold first. Either way the loop must
    stop well short of its iteration budget and say why.
    """
    model, _, update = _drive_worker(
        monkeypatch,
        {"error": True, "reason": "tool_failure", "message": "boom"},
    )

    assert model.calls < settings.max_tool_iterations
    assert any("stopped early" in e for e in update["tool_errors"])


def test_a_burst_of_failures_in_one_round_trips_the_error_breaker(monkeypatch):
    """The case no-progress alone would not catch until the following round."""
    model, session, update = _drive_worker(
        monkeypatch,
        {"error": True, "reason": "tool_failure", "message": "boom"},
        calls_per_turn=settings.max_consecutive_tool_errors,
    )

    # One round was enough to hit the consecutive-error threshold.
    assert model.calls == 1
    assert session.calls == settings.max_consecutive_tool_errors
    assert any("consecutive tool errors" in e for e in update["tool_errors"])


def test_calls_that_return_nothing_useful_break_the_loop(monkeypatch):
    """Succeeds every time, produces no evidence — the subtler runaway."""
    model, _, update = _drive_worker(
        monkeypatch, {"match_count": 0, "evidence": [], "note": "nothing matches"}
    )

    assert model.calls <= settings.max_unproductive_iterations + 1
    assert model.calls < settings.max_tool_iterations
    assert any("no new results" in e for e in update["tool_errors"])


def test_a_broken_loop_still_advances_the_dispatch_queue(monkeypatch):
    """Stopping early must not strand the turn on the same worker forever."""
    _, _, update = _drive_worker(
        monkeypatch, {"error": True, "reason": "tool_failure", "message": "boom"}
    )
    assert update["dispatch_index"] == 1


def test_llm_calls_are_counted_back_into_state(monkeypatch):
    model, _, update = _drive_worker(
        monkeypatch, {"match_count": 0, "evidence": []}
    )
    assert update["llm_calls"] == model.calls
