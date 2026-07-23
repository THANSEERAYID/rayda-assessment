"""Validate model claims against the evidence ledger.

This is the gate that turns "the model says it has evidence" into "the citation
resolves to a record the tool actually returned". A claim survives only if every
id it cites exists in the ledger; claims citing nothing at all are rejected too,
since an uncited sentence is exactly the unsupported conclusion the brief asks us
to avoid.

Numbers get an extra check. When a claim's text contains a figure, at least one
of its cited records must actually contain that figure — otherwise a model can
cite a real device and then attach a fabricated percentage to it, which reads as
grounded but is not.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..domain.models import Claim, Evidence, GroundedAnswer
from .ledger import EvidenceLedger

# Percentages, counts, capacities. Bare years/ids are filtered out below.
_NUMBER = re.compile(r"\d+(?:\.\d+)?")

# Timestamps are stripped before figures are extracted. A claim is *encouraged*
# to say when a reading was taken, but "2026-06-12T09:02:00" is not a set of
# quantities needing support — without this, the year, month and seconds are
# each treated as an unsupported figure and every well-formed answer citing a
# timestamp gets rejected.
_MONTH = (
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
)
_TIMESTAMP = re.compile(
    # ISO, which is how the evidence catalogue renders a reading...
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?"
    r"|\d{2}:\d{2}(?::\d{2})?"
    # ...and the forms a person writes, which is what the answer now uses:
    # "12 June 2026", "June 12, 2026", "June 2026", "12 June".
    rf"|\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}(?:\s+\d{{4}})?"
    rf"|{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}"
    rf"|{_MONTH}\s+\d{{4}}",
    re.IGNORECASE,
)

# Anything that reads as a name rather than a quantity: hostnames
# ("acme-macbook-4"), serials ("1LYSSFD074BB"), field names ("disk_free_pct"),
# models ("MacBook Pro"). Now that answers name devices the way people do, the
# trailing digit in a hostname would otherwise be read as an unsupported figure.
_IDENTIFIER = re.compile(r"[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*")


@dataclass
class ValidationResult:
    valid_claims: list[Claim] = field(default_factory=list)
    rejected: list[tuple[Claim, str]] = field(default_factory=list)
    cited_evidence: list[Evidence] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.rejected and bool(self.valid_claims)

    @property
    def rejection_summary(self) -> str:
        return "; ".join(f"{reason}: {claim.text[:80]}" for claim, reason in self.rejected)


def _strip_identifiers(text: str) -> str:
    """Blank out tokens containing a letter — they name things, not quantities."""
    return _IDENTIFIER.sub(
        lambda m: " " if any(c.isalpha() for c in m.group()) else m.group(), text
    )


def _numbers_in(text: str) -> set[float]:
    """Figures worth checking, ignoring tokens that are not really quantities."""
    text = _strip_identifiers(_TIMESTAMP.sub(" ", text))
    values: set[float] = set()
    for match in _NUMBER.finditer(text):
        token = match.group()
        # Skip identifiers embedded in words (device ids, "macOS 15.4" is fine
        # but "1LYSSFD074BB" should not contribute digits).
        start, end = match.span()
        if start > 0 and text[start - 1].isalnum():
            continue
        if end < len(text) and text[end].isalnum():
            continue
        values.add(float(token))
    return values


def _numbers_available(records: list[Evidence]) -> set[float]:
    values: set[float] = set()
    for record in records:
        # A device's own name may carry a figure — "Dell XPS 15", "ThinkPad X1".
        # An answer that names the device is entitled to repeat it.
        if record.device_label:
            values |= _numbers_in(record.device_label)
        for candidate in [record.value, *record.detail.values()]:
            if isinstance(candidate, bool):
                continue
            if isinstance(candidate, (int, float)):
                values.add(float(candidate))
            elif isinstance(candidate, str):
                values |= _numbers_in(candidate)
    return values


def _matches_within_tolerance(wanted: float, available: set[float]) -> bool:
    """Allow for rounding — "2.0% free" citing a stored 2.04 is still grounded."""
    for value in available:
        if abs(value - wanted) <= max(0.05, abs(wanted) * 0.01):
            return True
        # A claim may round a stored figure to a whole number.
        if round(value) == round(wanted) and abs(value - wanted) < 1.0:
            return True
    return False


def validate_answer(
    answer: GroundedAnswer,
    ledger: EvidenceLedger,
    *,
    check_numbers: bool = True,
) -> ValidationResult:
    """Split an answer's claims into grounded and rejected."""
    result = ValidationResult()
    seen: dict[str, Evidence] = {}

    for claim in answer.claims:
        unknown = [eid for eid in claim.evidence_ids if not ledger.has(eid)]
        if unknown:
            result.rejected.append(
                (claim, f"cites unknown evidence {', '.join(sorted(unknown))}")
            )
            continue
        if not claim.evidence_ids:
            result.rejected.append((claim, "no supporting evidence cited"))
            continue

        records = ledger.subset(claim.evidence_ids)

        if check_numbers:
            wanted = _numbers_in(claim.text)
            if wanted:
                available = _numbers_available(records)
                unsupported = {
                    n for n in wanted if not _matches_within_tolerance(n, available)
                }
                # A count the agent derived by tallying evidence ("6 devices")
                # legitimately will not appear inside any single record.
                derived = {float(len(records)), float(len({r.device_id for r in records}))}
                unsupported -= derived
                if unsupported:
                    result.rejected.append(
                        (
                            claim,
                            "figure(s) not present in cited evidence: "
                            + ", ".join(str(n) for n in sorted(unsupported)),
                        )
                    )
                    continue

        result.valid_claims.append(claim)
        for record in records:
            seen.setdefault(record.evidence_id, record)

    result.cited_evidence = list(seen.values())
    return result


def validate_action_evidence(
    evidence_ids: list[str], ledger: EvidenceLedger
) -> list[Evidence]:
    """Resolve the evidence backing a proposed action.

    Raises when nothing resolves — an action with no verifiable justification
    must be refused rather than surfaced to an administrator for approval.
    """
    from ..domain.errors import InsufficientEvidence

    if not evidence_ids:
        raise InsufficientEvidence(
            "This action was proposed without citing any telemetry to justify it."
        )
    resolved = ledger.subset(evidence_ids)
    if not resolved:
        raise InsufficientEvidence(
            "None of the evidence cited for this action could be resolved "
            "against the telemetry retrieved in this turn.",
            cited=evidence_ids,
        )
    return resolved
