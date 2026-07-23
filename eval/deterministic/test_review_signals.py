"""Objective review signals for the approver.

These are facts the system observed, not a confidence the model reported about
itself. That distinction is the point: a model asserting high confidence beside
a weak proposal actively misleads a reviewer, whereas "rests on a single reading
that does not describe what this action addresses" is checkable.

Nothing here gates anything. Approval remains required for every action
regardless of what these say — the signals exist so a queue can be triaged
instead of rubber-stamped.
"""
from __future__ import annotations

from fleet_copilot.domain.enums import ActionType
from fleet_copilot.domain.models import ProposedAction
from fleet_copilot.evidence.ledger import build_evidence
from fleet_copilot.evidence.review import assess_answer, assess_proposal


def _action(action_type: ActionType, evidence_ids: list[str], device="DEV-1"):
    return ProposedAction(
        action_id="act-1",
        thread_id="t-1",
        company_id="acme-001",
        action_type=action_type,
        target_device_id=device,
        justification="because",
        evidence_ids=evidence_ids,
    )


def _record(field: str, value, device="DEV-1"):
    return build_evidence(
        tool="run_insight_scan", field=field, value=value, device_id=device
    )


# ---------------------------------------------------------------------------
# Answer quality
# ---------------------------------------------------------------------------
def test_a_clean_turn_is_not_flagged():
    quality = assess_answer(
        claims_kept=3,
        rejected_claims=[],
        grounding_retries=0,
        tool_errors=[],
        evidence_records=12,
    )

    assert quality.degraded is False
    assert quality.notes == []
    assert quality.claims_kept == 3


def test_a_correction_is_reported():
    quality = assess_answer(
        claims_kept=2,
        rejected_claims=[],
        grounding_retries=1,
        tool_errors=[],
        evidence_records=8,
    )

    assert quality.degraded is True
    assert any("correction" in n for n in quality.notes)


def test_dropped_claims_are_reported():
    """The answer stays grounded, but the reader should know it was trimmed."""
    quality = assess_answer(
        claims_kept=1,
        rejected_claims=[{"text": "x", "reason": "unknown evidence"}],
        grounding_retries=0,
        tool_errors=[],
        evidence_records=4,
    )

    assert quality.claims_rejected == 1
    assert any("dropped as unsupported" in n for n in quality.notes)


def test_retrieval_failures_warn_about_coverage():
    quality = assess_answer(
        claims_kept=2,
        rejected_claims=[],
        grounding_retries=0,
        tool_errors=["query_devices: boom"],
        evidence_records=3,
    )

    assert quality.degraded is True
    assert any("may not cover everything" in n for n in quality.notes)


# ---------------------------------------------------------------------------
# Per-proposal signal
# ---------------------------------------------------------------------------
def test_a_well_supported_proposal_is_routine():
    records = [
        _record("battery.condition", "Service Recommended"),
        _record("battery.cycle_count", 1157),
    ]
    by_id = {r.evidence_id: r.model_dump(mode="json") for r in records}
    action = _action(ActionType.FLAG_DEVICE_FOR_REPLACEMENT, list(by_id))

    signal = assess_proposal(action, by_id)

    assert signal.supports_action_directly is True
    assert signal.evidence_count == 2
    assert signal.review_priority == "routine"
    assert signal.notes == []


def test_a_single_reading_is_flagged_for_a_closer_look():
    record = _record("battery.condition", "Service Recommended")
    by_id = {record.evidence_id: record.model_dump(mode="json")}
    action = _action(ActionType.FLAG_DEVICE_FOR_REPLACEMENT, list(by_id))

    signal = assess_proposal(action, by_id)

    assert signal.review_priority == "check_carefully"
    assert any("single reading" in n for n in signal.notes)


def test_evidence_that_does_not_address_the_action_is_called_out():
    """Identity evidence proves the device exists, not that it needs replacing."""
    records = [_record("device_identity.model_name", "MacBook Pro"), _record("employee_id", "emp-1")]
    by_id = {r.evidence_id: r.model_dump(mode="json") for r in records}
    action = _action(ActionType.FLAG_DEVICE_FOR_REPLACEMENT, list(by_id))

    signal = assess_proposal(action, by_id)

    assert signal.supports_action_directly is False
    assert signal.review_priority == "check_carefully"
    assert any("does not directly describe" in n for n in signal.notes)


def test_evidence_about_another_device_is_called_out():
    record = _record("battery.condition", "Service Recommended", device="DEV-OTHER")
    by_id = {record.evidence_id: record.model_dump(mode="json")}
    action = _action(ActionType.FLAG_DEVICE_FOR_REPLACEMENT, list(by_id), device="DEV-1")

    signal = assess_proposal(action, by_id)

    assert any("names the device being acted on" in n for n in signal.notes)


def test_notify_employee_is_not_held_to_a_field_requirement():
    """It changes nothing on the fleet, so any reading is a fair reason."""
    records = [_record("disk_free_pct", 2.0), _record("employee_id", "emp-1")]
    by_id = {r.evidence_id: r.model_dump(mode="json") for r in records}
    action = ProposedAction(
        action_id="act-2",
        thread_id="t-1",
        company_id="acme-001",
        action_type=ActionType.NOTIFY_EMPLOYEE,
        target_employee_id="emp-1",
        justification="heads up",
        evidence_ids=list(by_id),
    )

    signal = assess_proposal(action, by_id)
    assert signal.supports_action_directly is True


def test_unresolvable_evidence_ids_are_simply_absent_from_the_count():
    action = _action(ActionType.FLAG_DEVICE_FOR_REPLACEMENT, ["ev-nope"])

    signal = assess_proposal(action, {})

    assert signal.evidence_count == 0
    assert signal.supports_action_directly is False


def test_the_signal_never_gates_anything():
    """A weak proposal is still a proposal — the reviewer decides, not this."""
    record = _record("device_identity.model_name", "MacBook Pro")
    by_id = {record.evidence_id: record.model_dump(mode="json")}
    action = _action(ActionType.FLAG_DEVICE_FOR_REPLACEMENT, list(by_id))

    signal = assess_proposal(action, by_id)

    assert signal.review_priority == "check_carefully"
    # The action itself is untouched: still proposed, still awaiting a human.
    assert action.status.value == "proposed"
