"""Grounding enforcement.

Each test here stands in for a way a model can produce something that reads as
grounded but is not: citing an id that does not exist, citing nothing at all, or
citing a real record and attaching a fabricated number to it.
"""
from __future__ import annotations

from datetime import datetime

from fleet_copilot.domain.models import Claim, GroundedAnswer
from fleet_copilot.evidence.ledger import (
    EvidenceLedger,
    build_evidence,
    make_evidence_id,
)
from fleet_copilot.evidence.validator import validate_answer


def _ledger() -> tuple[EvidenceLedger, str, str]:
    ledger = EvidenceLedger()
    first = build_evidence(
        tool="query_devices",
        field="disk_free_pct",
        value=2.04,
        device_id="DEV-1",
        snapshot_ts=datetime(2026, 6, 12, 9, 0),
    )
    second = build_evidence(
        tool="query_devices",
        field="disk_free_pct",
        value=23.8,
        device_id="DEV-2",
        snapshot_ts=datetime(2026, 6, 12, 9, 0),
    )
    ledger.add(first)
    ledger.add(second)
    return ledger, first.evidence_id, second.evidence_id


def test_evidence_ids_are_deterministic():
    ts = datetime(2026, 6, 12, 9, 0)
    assert make_evidence_id("t", "DEV-1", ts, "f") == make_evidence_id("t", "DEV-1", ts, "f")


def test_evidence_ids_distinguish_different_facts():
    ts = datetime(2026, 6, 12, 9, 0)
    assert make_evidence_id("t", "DEV-1", ts, "disk") != make_evidence_id(
        "t", "DEV-1", ts, "ram"
    )
    assert make_evidence_id("t", "DEV-1", ts, "disk") != make_evidence_id(
        "t", "DEV-2", ts, "disk"
    )


def test_a_grounded_claim_is_accepted():
    ledger, first, _ = _ledger()
    answer = GroundedAnswer(
        answer="DEV-1 is nearly full.",
        claims=[Claim(text="DEV-1 has 2.04% free space.", evidence_ids=[first])],
    )
    result = validate_answer(answer, ledger)

    assert result.ok
    assert len(result.valid_claims) == 1
    assert result.cited_evidence[0].evidence_id == first


def test_fabricated_evidence_id_is_rejected():
    ledger, _, _ = _ledger()
    answer = GroundedAnswer(
        answer="DEV-9 is nearly full.",
        claims=[Claim(text="DEV-9 has 1% free.", evidence_ids=["ev-doesnotexist"])],
    )
    result = validate_answer(answer, ledger)

    assert not result.ok
    assert not result.valid_claims
    assert "unknown evidence" in result.rejected[0][1]


def test_uncited_claim_is_rejected():
    ledger, _, _ = _ledger()
    answer = GroundedAnswer(
        answer="Things look fine.",
        claims=[Claim(text="The fleet is healthy.", evidence_ids=[])],
    )
    result = validate_answer(answer, ledger)

    assert not result.ok
    assert "no supporting evidence" in result.rejected[0][1]


def test_number_not_present_in_cited_evidence_is_rejected():
    """The subtle failure: a real citation with an invented figure attached."""
    ledger, first, _ = _ledger()
    answer = GroundedAnswer(
        answer="DEV-1 is low.",
        claims=[Claim(text="DEV-1 has 47.5% free space.", evidence_ids=[first])],
    )
    result = validate_answer(answer, ledger)

    assert not result.ok
    assert "not present in cited evidence" in result.rejected[0][1]


def test_rounding_a_cited_figure_is_still_grounded():
    ledger, first, _ = _ledger()
    answer = GroundedAnswer(
        answer="DEV-1 is low.",
        claims=[Claim(text="DEV-1 has about 2.0% free space.", evidence_ids=[first])],
    )
    assert validate_answer(answer, ledger).ok


def test_a_count_derived_from_the_cited_records_is_grounded():
    ledger, first, second = _ledger()
    answer = GroundedAnswer(
        answer="Two devices were checked.",
        claims=[
            Claim(text="2 devices were examined.", evidence_ids=[first, second])
        ],
    )
    assert validate_answer(answer, ledger).ok


def test_valid_claims_survive_alongside_rejected_ones():
    ledger, first, _ = _ledger()
    answer = GroundedAnswer(
        answer="Mixed.",
        claims=[
            Claim(text="DEV-1 has 2.04% free space.", evidence_ids=[first]),
            Claim(text="DEV-3 is also failing.", evidence_ids=["ev-nope"]),
        ],
    )
    result = validate_answer(answer, ledger)

    assert len(result.valid_claims) == 1
    assert len(result.rejected) == 1
    assert not result.ok


def test_device_identifiers_are_not_treated_as_figures():
    """Digits inside an id must not be mistaken for an unsupported number."""
    ledger = EvidenceLedger()
    record = build_evidence(
        tool="query_devices",
        field="model_name",
        value="MacBook Pro",
        device_id="1LYSSFD074BB",
    )
    ledger.add(record)
    answer = GroundedAnswer(
        answer="Device found.",
        claims=[
            Claim(
                text="1LYSSFD074BB is a MacBook Pro.",
                evidence_ids=[record.evidence_id],
            )
        ],
    )
    assert validate_answer(answer, ledger).ok


def test_ledger_deduplicates_the_same_fact():
    ledger = EvidenceLedger()
    for _ in range(3):
        ledger.add(
            build_evidence(
                tool="query_devices",
                field="disk_free_pct",
                value=2.0,
                device_id="DEV-1",
            )
        )
    assert len(ledger) == 1


def test_a_timestamp_in_a_claim_is_not_treated_as_a_figure():
    """Regression: the model is asked to cite when a reading was taken.

    Without stripping timestamps, "2026-06-12T09:02:00" contributed 2026, 6, 2
    and 0 as figures needing support, so essentially every well-formed answer
    was rejected as ungrounded.
    """
    ledger, first, _ = _ledger()
    answer = GroundedAnswer(
        answer="DEV-1 is nearly full.",
        claims=[
            Claim(
                text="DEV-1 has 2.04% free disk space as of 2026-06-12T09:02:00.",
                evidence_ids=[first],
            )
        ],
    )
    result = validate_answer(answer, ledger)

    assert result.ok, result.rejection_summary


def test_a_plain_date_is_also_ignored():
    ledger, first, _ = _ledger()
    answer = GroundedAnswer(
        answer="Reading taken recently.",
        claims=[
            Claim(text="DEV-1 was at 2.04% on 2026-06-12.", evidence_ids=[first])
        ],
    )
    assert validate_answer(answer, ledger).ok


def test_stripping_timestamps_does_not_hide_a_fabricated_figure():
    """The check must still bite on the number that matters."""
    ledger, first, _ = _ledger()
    answer = GroundedAnswer(
        answer="DEV-1.",
        claims=[
            Claim(
                text="DEV-1 has 91.7% free space as of 2026-06-12T09:02:00.",
                evidence_ids=[first],
            )
        ],
    )
    result = validate_answer(answer, ledger)

    assert not result.ok
    assert "91.7" in result.rejection_summary


def test_a_natural_language_date_is_not_treated_as_a_figure():
    """Answers now name dates the way people write them, not in ISO."""
    ledger, first, _ = _ledger()
    answer = GroundedAnswer(
        answer="DEV-1 is nearly full.",
        claims=[
            Claim(
                text="DEV-1 has 2.04% free space as of 12 June 2026.",
                evidence_ids=[first],
            )
        ],
    )
    assert validate_answer(answer, ledger).ok, validate_answer(answer, ledger).rejection_summary


def test_a_hostname_ending_in_a_digit_is_not_a_figure():
    """Regression: "acme-macbook-4" contributed 4 as an unsupported number."""
    ledger = EvidenceLedger()
    record = build_evidence(
        tool="query_devices",
        field="disk_free_pct",
        value=2.0,
        device_id="MT7PJB7N5LRE",
        device_label="acme-macbook-4 (MacBook Pro)",
    )
    ledger.add(record)
    answer = GroundedAnswer(
        answer="Low on disk.",
        claims=[
            Claim(
                text="acme-macbook-4 has 2.0% free space.",
                evidence_ids=[record.evidence_id],
            )
        ],
    )
    assert validate_answer(answer, ledger).ok


def test_a_figure_inside_a_model_name_is_available_from_the_label():
    """"Dell XPS 15" names a 15 the answer is entitled to repeat."""
    ledger = EvidenceLedger()
    record = build_evidence(
        tool="query_devices",
        field="disk_free_pct",
        value=2.0,
        device_id="M4XVHUV1MEPZ",
        device_label="acme-dell-9 (Dell XPS 15)",
    )
    ledger.add(record)
    answer = GroundedAnswer(
        answer="Low on disk.",
        claims=[
            Claim(
                text="The Dell XPS 15 acme-dell-9 has 2.0% free.",
                evidence_ids=[record.evidence_id],
            )
        ],
    )
    assert validate_answer(answer, ledger).ok


def test_readable_names_do_not_weaken_the_fabricated_figure_check():
    """The point of all this stripping is still to catch invented numbers."""
    ledger = EvidenceLedger()
    record = build_evidence(
        tool="query_devices",
        field="disk_free_pct",
        value=2.0,
        device_id="MT7PJB7N5LRE",
        device_label="acme-macbook-4 (MacBook Pro)",
    )
    ledger.add(record)
    answer = GroundedAnswer(
        answer="Plenty of room.",
        claims=[
            Claim(
                text="acme-macbook-4 has 91.7% free space as of 12 June 2026.",
                evidence_ids=[record.evidence_id],
            )
        ],
    )
    result = validate_answer(answer, ledger)

    assert not result.ok
    assert "91.7" in result.rejection_summary
