"""Terminal nodes: assemble the reply, or refuse."""
from __future__ import annotations

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from ...domain.enums import ActionStatus, AuditEventType
from ..state import AgentState
from ._common import record_audit, record_step, validated_node


@validated_node
async def respond_node(state: AgentState, config: RunnableConfig) -> dict:
    answer = state.answer
    executed = state.executed

    if executed:
        answer = f"{answer}\n\n{_describe_outcomes(executed)}".strip()

    seq = record_step(
        state,
        "respond",
        "ok",
        {
            "claims": len(state.claims),
            "evidence": len(state.evidence),
            "executed": len(executed),
        },
    )
    return {
        "answer": answer,
        "step_seq": seq,
        "awaiting_approval": False,
        "messages": [AIMessage(content=answer)],
    }


@validated_node
async def refuse_node(state: AgentState, config: RunnableConfig) -> dict:
    reason = state.refusal_reason or "tool_failure"
    message = state.refusal_message or "I cannot answer that from this telemetry."

    seq = record_step(state, "refuse", reason, {"message": message})
    record_audit(state, AuditEventType.REFUSAL, f"Refused: {reason}", {"message": message})
    return {
        "answer": message,
        "step_seq": seq,
        "awaiting_approval": False,
        "messages": [AIMessage(content=message)],
    }


def _describe_outcomes(executed: list[dict]) -> str:
    """Report what approval actually did, per action."""
    done = [a for a in executed if a.get("status") == ActionStatus.EXECUTED.value]
    refused = [a for a in executed if a.get("status") == ActionStatus.REJECTED.value]

    lines: list[str] = []
    if done:
        lines.append("Carried out after your approval:")
        lines.extend(f"- {a.get('result')}" for a in done)
    if refused:
        lines.append("Rejected, so nothing was done:")
        lines.extend(
            f"- {a['action_type'].replace('_', ' ')} on "
            f"{a.get('target_label') or a.get('target_device_id') or a.get('target_employee_id')}"
            for a in refused
        )
    return "\n".join(lines)
