"""Objective signals for the human reviewing a turn.

Deliberately **not** a confidence score from the model. A model asserting "95%
confident" beside a fabricated citation actively misleads a reviewer, and
self-reported confidence is poorly calibrated in exactly the cases where it
matters most.

What is reported instead is what the system observed while producing the answer:
how many claims survived grounding, whether the model needed correcting, whether
a loop was cut short, and — per proposal — how directly the cited evidence
speaks to the action being proposed.

This changes nothing about the approval gate, which stays unconditional. Its
purpose is triage: shown five proposals, an administrator should be able to see
which rest on a direct reading and which scraped through, rather than having to
reconstruct that from the justifications. Approval fatigue is how these systems
fail in practice.
"""
from __future__ import annotations

from ..domain.action_policy import supports_directly
from ..domain.enums import ActionType
from ..domain.models import AnswerQuality, Evidence, ProposedAction, ReviewSignal


def assess_answer(
    *,
    claims_kept: int,
    rejected_claims: list[dict],
    grounding_retries: int,
    tool_errors: list[str],
    evidence_records: int,
    charts_rejected: int = 0,
) -> AnswerQuality:
    """Summarise how cleanly this turn produced its answer."""
    notes: list[str] = []

    if grounding_retries:
        notes.append(
            f"The answer needed {grounding_retries} correction"
            f"{'s' if grounding_retries > 1 else ''} before every statement "
            "resolved to evidence."
        )
    if rejected_claims:
        notes.append(
            f"{len(rejected_claims)} statement"
            f"{'s were' if len(rejected_claims) > 1 else ' was'} dropped as "
            "unsupported and are not in the answer."
        )
    if tool_errors:
        notes.append(
            f"{len(tool_errors)} retrieval step"
            f"{'s' if len(tool_errors) > 1 else ''} failed, so the answer may "
            "not cover everything relevant."
        )
    if charts_rejected:
        notes.append(f"{charts_rejected} chart(s) could not be resolved and were dropped.")

    # "Degraded" means the turn did not go cleanly — the answer is still
    # grounded (anything ungrounded was removed), but it is worth a closer read.
    degraded = bool(grounding_retries or rejected_claims or tool_errors)

    return AnswerQuality(
        claims_kept=claims_kept,
        claims_rejected=len(rejected_claims),
        grounding_retries=grounding_retries,
        tool_errors=len(tool_errors),
        evidence_records=evidence_records,
        degraded=degraded,
        notes=notes,
    )


def assess_proposal(
    action: ProposedAction, evidence_by_id: dict[str, dict]
) -> ReviewSignal:
    """Describe how well-supported one proposal is, in checkable terms."""
    records = [
        Evidence.model_validate(evidence_by_id[eid])
        for eid in action.evidence_ids
        if eid in evidence_by_id
    ]
    fields = sorted({r.field for r in records})
    devices = {r.device_id for r in records if r.device_id}

    direct = supports_directly(ActionType(action.action_type), fields)
    notes: list[str] = []

    if not direct:
        # The service gate should make this unreachable for constrained action
        # types; if it appears, something bypassed the check.
        notes.append(
            "The cited evidence does not directly describe what this action "
            "addresses."
        )
    if len(records) == 1:
        notes.append("Rests on a single reading.")
    if action.target_device_id and action.target_device_id not in devices:
        notes.append("No cited evidence names the device being acted on.")

    # Anything unusual is worth a reviewer's attention; the common case is a
    # proposal backed by several on-point readings about the right device.
    priority = "routine" if direct and len(records) > 1 and not notes else "check_carefully"

    return ReviewSignal(
        evidence_count=len(records),
        distinct_fields=fields,
        supports_action_directly=direct,
        review_priority=priority,
        notes=notes,
    )
