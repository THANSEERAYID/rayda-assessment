"""The evidence ledger — the mechanism that makes grounding checkable.

The model is never allowed to invent a citation. Instead:

1. A tool produces rows. For each citable fact it emits an :class:`Evidence`
   record whose ``evidence_id`` is derived from the fact's *content*.
2. The executor collects every emitted record into this ledger for the turn.
3. The model may only cite ids that are already in the ledger.
4. The validator resolves each cited id and confirms the value still matches.

Content-derived ids matter for two reasons. They survive the MCP process
boundary without any shared mutable state — the tool server and the agent
compute the same id for the same fact independently. And they are stable across
runs, so an evaluation case can assert on exact citation ids rather than on
whichever counter a particular execution happened to reach.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Iterable

from ..domain.models import Evidence


def make_evidence_id(
    tool: str,
    device_id: str | None,
    snapshot_ts: datetime | str | None,
    field: str,
    discriminator: str = "",
) -> str:
    """Deterministic id for one citable fact.

    Derived from the fact's coordinates, not its value, so the validator can look
    an id up and then compare the value — a changed value must surface as a
    mismatch rather than silently becoming a different id.

    ``discriminator`` separates facts that share every coordinate but describe
    different things. An absence record has no device and no timestamp, so two
    unrelated empty queries would otherwise collide on a single id and the
    second would silently inherit the first's value.
    """
    if isinstance(snapshot_ts, datetime):
        ts = snapshot_ts.isoformat()
    else:
        ts = snapshot_ts or ""
    payload = "|".join([tool, device_id or "", ts, field, discriminator])
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10]
    return f"ev-{digest}"


def build_evidence(
    *,
    tool: str,
    field: str,
    value: Any,
    device_id: str | None = None,
    snapshot_ts: datetime | None = None,
    detail: dict[str, Any] | None = None,
    discriminator: str = "",
    device_label: str | None = None,
) -> Evidence:
    return Evidence(
        evidence_id=make_evidence_id(
            tool, device_id, snapshot_ts, field, discriminator
        ),
        tool=tool,
        device_id=device_id,
        device_label=device_label,
        snapshot_ts=snapshot_ts,
        field=field,
        value=value,
        detail=detail or {},
    )


class EvidenceLedger:
    """Every fact the agent was shown during one turn.

    Insertion-ordered so the prompt renders citations in the order they were
    retrieved, which keeps model output stable between identical runs.
    """

    def __init__(self) -> None:
        self._items: dict[str, Evidence] = {}

    def add(self, evidence: Evidence) -> Evidence:
        # Re-observing the same fact is a no-op; the first record wins so an id
        # always resolves to one value within a turn.
        self._items.setdefault(evidence.evidence_id, evidence)
        return self._items[evidence.evidence_id]

    def extend(self, items: Iterable[Evidence]) -> None:
        for item in items:
            self.add(item)

    def get(self, evidence_id: str) -> Evidence | None:
        return self._items.get(evidence_id)

    def has(self, evidence_id: str) -> bool:
        return evidence_id in self._items

    def all(self) -> list[Evidence]:
        return list(self._items.values())

    def ids(self) -> set[str]:
        return set(self._items)

    def subset(self, evidence_ids: Iterable[str]) -> list[Evidence]:
        """Resolve ids to records, skipping any that are unknown."""
        return [self._items[i] for i in evidence_ids if i in self._items]

    def render_for_prompt(self, limit: int | None = None) -> str:
        """The citation catalogue shown to the model.

        The model picks ids from this list; anything it writes that is not here
        is rejected downstream.
        """
        items = self.all()
        if limit is not None:
            items = items[:limit]
        if not items:
            return "(no evidence retrieved)"
        return "\n".join(item.summary() for item in items)

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, evidence_id: object) -> bool:
        return evidence_id in self._items
