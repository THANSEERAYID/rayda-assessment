"""Manager node — decide which specialized agents run, and in what order.

The model proposes a dispatch; :func:`normalize_dispatch` then repairs it in
code. The repair is the point: an action turn dispatched without a discovery
agent would leave ``action_agent`` with nothing to cite and every proposal would
be refused, so that invariant is enforced structurally rather than left to the
model getting the prompt right.
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from ...domain.enums import Intent
from ..llm import get_llm, invoke_llm
from ..state import AgentState
from ..workers import WorkerName, describe_workers
from ._common import Timer, load_prompt, record_step, validated_node


class Dispatch(BaseModel):
    agents: list[WorkerName] = Field(
        min_length=1,
        max_length=2,
        description=(
            "Ordered agents to run. One for a question, two for an action "
            "(a discovery agent, then action_agent)."
        ),
    )
    reason: str = Field(description="One sentence on why this dispatch.")


def normalize_dispatch(
    agents: list[WorkerName], intent: str
) -> tuple[list[WorkerName], list[str]]:
    """Enforce the dispatch invariants. Returns the queue and any repairs made.

    Pure function — unit-tested without a model.
    """
    repairs: list[str] = []

    # Preserve order while removing repeats; a worker running twice in one turn
    # would just re-do its own work.
    seen: list[WorkerName] = []
    for agent in agents:
        if agent not in seen:
            seen.append(agent)
    if len(seen) != len(agents):
        repairs.append("removed duplicate agents")

    if WorkerName.ACTION in seen:
        # action_agent acts on what it is handed, so it must run last.
        if seen[-1] is not WorkerName.ACTION:
            seen = [a for a in seen if a is not WorkerName.ACTION] + [WorkerName.ACTION]
            repairs.append("moved action_agent last")

        # It holds no discovery or evidence-emitting tools, so alone it can
        # cite nothing and every proposal it made would be refused.
        if len(seen) == 1:
            discovery = (
                WorkerName.INSIGHT
                if intent == Intent.INSIGHT.value
                else WorkerName.QA
            )
            seen = [discovery, WorkerName.ACTION]
            repairs.append(f"prepended {discovery.value} — action_agent cannot discover")

    if len(seen) > 2:
        seen = seen[:2]
        repairs.append("clamped to 2 agents")

    return seen, repairs


@validated_node
async def manager_node(state: AgentState, config: RunnableConfig) -> dict:
    intent = state.intent
    plan_steps = state.plan

    with Timer() as timer:
        model = get_llm().with_structured_output(Dispatch)
        result: Dispatch = await invoke_llm(
            model,
            [
                SystemMessage(
                    content=load_prompt("manager").replace(
                        "{roster}", describe_workers()
                    )
                ),
                HumanMessage(
                    content=(
                        f"Question: {state.question}\n"
                        f"Planner intent: {intent}\n"
                        f"Planner rationale: {state.plan_rationale}\n\n"
                        "Retrieval plan:\n"
                        + "\n".join(f"- {s}" for s in plan_steps)
                    )
                ),
            ],
            calls_so_far=state.llm_calls,
        )

    queue, repairs = normalize_dispatch(result.agents, intent)

    seq = record_step(
        state,
        "manager",
        "ok",
        {
            "requested": [a.value for a in result.agents],
            "dispatched": [a.value for a in queue],
            "reason": result.reason,
            "repairs": repairs,
        },
        timer.elapsed_ms,
    )

    return {
        "dispatch_queue": [a.value for a in queue],
        "dispatch_index": 0,
        "dispatch_reason": result.reason,
        "step_seq": seq,
        "llm_calls": state.llm_calls + 1,
    }
