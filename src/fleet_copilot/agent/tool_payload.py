"""Shaping tool results for the model's context window.

A tool result serves two consumers with different appetites. The evidence ledger
needs it whole: the grounding validator checks a claimed figure against each
record's ``detail`` as well as its ``value`` (``evidence/validator.py``), so a
disk-space claim can be grounded on a battery record that carries the disk
reading alongside. Drop ``detail`` there and grounding quietly weakens.

The model needs far less — enough to decide what to do next, and enough to cite.
It also pays for every byte repeatedly, because each pass of the agent loop
re-sends the entire message list. A single ``query_devices`` over seven devices
is ~3,300 tokens, and two thirds of that is an ``evidence`` array restating the
``matches`` array beside it.

So the ledger keeps the payload intact and these two functions shape the copy
that goes into the conversation. Both run *after* ``_ingest`` has mined the
result, so neither can affect what is citable.

``trim_for_model`` is the lossless-in-practice pass applied to a fresh result.
``digest`` is the lossy pass applied once a round is superseded: by then the
agent has already acted on the detail, and all that needs to survive is what it
would quote.
"""
from __future__ import annotations

from typing import Any

# Kept in a digest because answers quote them; everything else about a device is
# reachable through the ledger and, if it is not evidence, cannot be cited.
_DIGEST_EVIDENCE_KEYS = ("evidence_id", "device_label", "device_id", "field", "value")

# Small scalars worth preserving verbatim in a digest: they carry the shape of
# the result ("nothing matched") rather than its contents.
_DIGEST_SCALARS = (
    "match_count",
    "total_devices_considered",
    "as_of_mode",
    "note",
    "error",
    "reason",
    "message",
)


def trim_for_model(result: Any) -> Any:
    """Drop from a tool result what the model is already being told elsewhere.

    Removes each evidence record's ``detail`` — every value in it also appears on
    the matching row of the sibling collection — and its ``device_label`` when
    that label is already carried by a sibling row. Nothing that identifies or
    values a reading is touched, so the model can still cite everything.
    """
    if not isinstance(result, dict):
        return result

    records = result.get("evidence")
    if not isinstance(records, list):
        return result

    labels_elsewhere = _labels_outside_evidence(result)
    trimmed: list[Any] = []
    for record in records:
        if not isinstance(record, dict):
            trimmed.append(record)
            continue
        slim = {k: v for k, v in record.items() if k != "detail"}
        if slim.get("device_label") in labels_elsewhere:
            slim.pop("device_label", None)
        trimmed.append(slim)

    return {**result, "evidence": trimmed}


def digest(result: Any, *, tool: str | None = None) -> Any:
    """Condense a superseded tool result to what the agent might still quote.

    Called on results from rounds the agent has already reasoned about. Their
    full payloads are re-sent on every later pass of the loop, which is what
    makes a third iteration cost more than the first two together.

    Evidence collapses from one JSON object per record to one line per record —
    the same facts without the key names repeated ~300 characters at a time.
    """
    if not isinstance(result, dict):
        return result

    condensed: dict[str, Any] = {}
    if tool:
        condensed["tool"] = tool
    for key in _DIGEST_SCALARS:
        if key in result:
            condensed[key] = result[key]

    for key in ("matches", "findings", "proposals", "actions", "checks", "points"):
        value = result.get(key)
        if isinstance(value, list) and value:
            condensed[f"{key}_count"] = len(value)

    records = result.get("evidence")
    if isinstance(records, list) and records:
        condensed["evidence"] = [_evidence_line(r) for r in records]

    condensed["condensed"] = (
        "Earlier result, shortened. Every citable reading is listed; "
        "call the tool again if you need the full rows."
    )
    return condensed


def _labels_outside_evidence(result: dict) -> set[str]:
    """Device labels the model can already read off a non-evidence row."""
    labels: set[str] = set()
    for key, value in result.items():
        if key == "evidence" or not isinstance(value, list):
            continue
        for row in value:
            if isinstance(row, dict) and isinstance(row.get("device_label"), str):
                labels.add(row["device_label"])
    return labels


def _evidence_line(record: Any) -> Any:
    if not isinstance(record, dict):
        return record
    parts = [str(record.get("evidence_id", "?"))]
    name = record.get("device_label") or record.get("device_id")
    if name:
        parts.append(str(name))
    parts.append(f"{record.get('field', '?')}={record.get('value')!r}")
    if record.get("snapshot_ts"):
        parts.append(f"@{record['snapshot_ts']}")
    return " | ".join(parts)
