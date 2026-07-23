"""Planning node — classify the request and decide what to retrieve."""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from ...domain.enums import Intent
from ..llm import get_llm, invoke_llm
from ..state import AgentState
from ._common import Timer, load_prompt, record_step, validated_node


class Plan(BaseModel):
    intent: Intent = Field(description="qa | insight | action | out_of_scope")
    rationale: str = Field(description="One sentence on why this intent.")
    steps: list[str] = Field(
        default_factory=list,
        description="Two to four retrieval steps naming the tools to use.",
    )
    unanswerable_because: str | None = Field(
        default=None,
        description=(
            "Set only when the telemetry cannot answer this, explaining what is "
            "missing. Leave null otherwise."
        ),
    )


@validated_node
async def plan_node(state: AgentState, config: RunnableConfig) -> dict:
    with Timer() as timer:
        model = get_llm().with_structured_output(Plan)
        result: Plan = await invoke_llm(
            model,
            [
                SystemMessage(content=load_prompt("planner")),
                HumanMessage(content=state.question),
            ],
            calls_so_far=state.llm_calls,
        )

    seq = record_step(
        state,
        "plan",
        "ok",
        {
            # The question is recorded on the first step of the turn so a trace
            # can be read back as "this was asked, then this happened" without
            # joining against the conversation.
            "question": state.question,
            "intent": result.intent.value,
            "rationale": result.rationale,
            "steps": result.steps,
            "unanswerable_because": result.unanswerable_because,
        },
        timer.elapsed_ms,
    )

    update: dict = {
        "intent": result.intent.value,
        "plan": result.steps,
        "plan_rationale": result.rationale,
        "step_seq": seq,
        "llm_calls": state.llm_calls + 1,
    }
    if result.intent is Intent.OUT_OF_SCOPE:
        update["refusal_reason"] = "out_of_scope"
        update["refusal_message"] = (
            result.unanswerable_because
            or "That is outside what this device telemetry can answer."
        )
    return update


def route_after_plan(state: AgentState) -> str:
    return "refuse" if state.refusal_reason else "manager"
