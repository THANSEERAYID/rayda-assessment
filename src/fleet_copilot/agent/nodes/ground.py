"""Grounding node — write the answer, then verify every claim against evidence.

The model produces a structured answer whose claims carry evidence ids. Those
ids are resolved against the ledger of what the tools actually returned. A claim
citing an unknown id, citing nothing, or attaching a figure absent from its cited
records is rejected.

On rejection the model gets one corrective attempt with the specific problem
quoted back. If it still cannot ground the answer, the turn refuses rather than
degrading into a plausible-sounding but unverified reply — an ungrounded answer
that looks confident is the failure mode this whole design exists to prevent.
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from ...config import settings
from ...domain.charts import describe_available_charts
from ...domain.enums import AuditEventType
from ...domain.models import Evidence, Finding, GroundedAnswer
from ...domain.text import sanitize_for_prompt
from ...evidence.chart_builder import resolve_charts
from ...evidence.ledger import EvidenceLedger
from ...evidence.validator import validate_answer
from ..llm import get_llm, invoke_llm
from ..state import AgentState
from ._common import Timer, load_prompt, record_audit, record_step, validated_node

# Long ledgers are truncated in the prompt; the validator still accepts any id.
_MAX_PROMPT_EVIDENCE = 220


def _rebuild_ledger(state: AgentState) -> EvidenceLedger:
    ledger = EvidenceLedger()
    for record in state.evidence.values():
        ledger.add(Evidence.model_validate(record))
    return ledger


def _context_block(state: AgentState, ledger: EvidenceLedger) -> str:
    parts = [f"Question: {state.question}", ""]

    findings = state.findings
    if findings:
        parts.append("Detector findings (figures already computed — use as given):")
        for finding in findings[:40]:
            parts.append(
                f"- {finding['finding_type']} on "
                f"{sanitize_for_prompt(finding.get('device_label') or finding['device_id'])} "
                f"[device_id {finding['device_id']}] "
                f"[{finding['severity']}]: {sanitize_for_prompt(finding['title'])} "
                f"metrics={sanitize_for_prompt(finding.get('metrics'), 400)} "
                f"evidence={finding.get('evidence_ids')}"
            )
        parts.append("")

    proposals = state.proposals
    if proposals:
        parts.append("Actions proposed this turn (awaiting human approval):")
        for action in proposals:
            parts.append(
                f"- {action['action_id']}: {action['action_type']} on "
                f"{action.get('target_device_id') or action.get('target_employee_id')}"
            )
        parts.append("")

    errors = state.tool_errors
    if errors:
        parts.append("Tool errors encountered:")
        parts.extend(f"- {e}" for e in errors[:8])
        parts.append("")

    evidence_fields = [record.field for record in ledger.all()]
    parts.append(
        describe_available_charts(
            findings_present=bool(findings),
            history_keys=sorted(state.history_series.keys()),
            evidence_fields=evidence_fields,
        )
    )
    parts.append("")

    parts.append(f"Evidence catalogue ({len(ledger)} records) — cite only these ids:")
    parts.append(ledger.render_for_prompt(limit=_MAX_PROMPT_EVIDENCE))
    if len(ledger) > _MAX_PROMPT_EVIDENCE:
        parts.append(f"... plus {len(ledger) - _MAX_PROMPT_EVIDENCE} more records.")
    return "\n".join(parts)


@validated_node
async def ground_node(state: AgentState, config: RunnableConfig) -> dict:
    ledger = _rebuild_ledger(state)
    seq = state.step_seq

    if not ledger:
        seq = record_step(state, "ground", "no_evidence", seq=seq)
        return {
            "step_seq": seq,
            "refusal_reason": "unanswerable_from_data",
            "refusal_message": (
                "I could not find telemetry that answers that. Nothing was "
                "retrieved, so there is nothing I can state with confidence."
            ),
        }

    model = get_llm().with_structured_output(GroundedAnswer)
    messages = [
        SystemMessage(content=load_prompt("grounder")),
        HumanMessage(content=_context_block(state, ledger)),
    ]

    attempts = 0
    max_attempts = settings.max_grounding_retries + 1
    result = None
    validation = None

    while attempts < max_attempts:
        attempts += 1
        with Timer() as timer:
            result: GroundedAnswer = await invoke_llm(
                model, messages, calls_so_far=state.llm_calls + attempts - 1
            )
        validation = validate_answer(result, ledger)

        seq = record_step(
            state,
            "ground",
            "ok" if validation.ok else "rejected",
            {
                "attempt": attempts,
                "claims": len(result.claims),
                "valid_claims": len(validation.valid_claims),
                "rejected": [
                    {"claim": c.text[:160], "reason": r} for c, r in validation.rejected
                ],
            },
            timer.elapsed_ms,
            seq=seq,
        )

        if validation.ok:
            break

        record_audit(
            state,
            AuditEventType.GROUNDING_REJECTED,
            f"Rejected {len(validation.rejected)} ungrounded claim(s) on attempt {attempts}",
            {"detail": validation.rejection_summary[:1000]},
        )

        if attempts < max_attempts:
            messages.append(
                HumanMessage(
                    content=(
                        "Some claims failed grounding validation and were rejected:\n"
                        + "\n".join(
                            f"- {reason} — \"{claim.text[:160]}\""
                            for claim, reason in validation.rejected
                        )
                        + "\n\nRewrite the answer. Cite only evidence ids from the "
                        "catalogue, and only state figures that appear in the "
                        "records you cite. Drop anything you cannot support."
                    )
                )
            )

    assert result is not None and validation is not None

    if validation.valid_claims:
        findings = [Finding.model_validate(f) for f in state.findings]
        chart_result = resolve_charts(
            result.charts,
            ledger=ledger,
            findings=findings,
            history_series=state.history_series,
        )
        if chart_result.rejected:
            seq = record_step(
                state,
                "ground.charts",
                "rejected",
                {
                    "rejected": [
                        {"chart": r.title, "reason": reason}
                        for r, reason in chart_result.rejected
                    ]
                },
                seq=seq,
                attach_llm_usage=False,
            )

        return {
            "step_seq": seq,
            "answer": result.answer,
            "claims": [c.model_dump() for c in validation.valid_claims],
            "rejected_claims": [
                {"text": c.text, "reason": r} for c, r in validation.rejected
            ],
            "grounding_retries": attempts - 1,
            "llm_calls": state.llm_calls + attempts,
            "charts": [c.model_dump(mode="json") for c in chart_result.charts],
            "rejected_charts": [
                {"title": r.title, "reason": reason} for r, reason in chart_result.rejected
            ],
        }

    return {
        "step_seq": seq,
        "grounding_retries": attempts - 1,
        "llm_calls": state.llm_calls + attempts,
        "rejected_claims": [
            {"text": c.text, "reason": r} for c, r in validation.rejected
        ],
        "refusal_reason": "ungrounded_claims",
        "refusal_message": (
            "I retrieved telemetry but could not produce an answer where every "
            "statement resolves to it, so I am not going to guess. "
            + (validation.rejection_summary[:300] if validation.rejected else "")
        ),
    }


def route_after_ground(state: AgentState) -> str:
    if state.refusal_reason:
        return "refuse"
    if state.proposals:
        return "approval"
    return "respond"
