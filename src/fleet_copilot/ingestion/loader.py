"""Read the NDJSON dataset into normalised snapshots."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from ..config import settings
from ..domain.models import DeviceSnapshot
from .normalize import normalize


def iter_raw_records(path: Path | None = None) -> Iterator[dict]:
    """Yield raw JSON objects from the newline-delimited dataset."""
    path = Path(path or settings.dataset_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Dataset not found at {path}. See the README for the download step."
        )
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:  # pragma: no cover - corrupt input
                raise ValueError(f"Malformed JSON on line {line_no} of {path}") from exc


def load_snapshots(path: Path | None = None) -> list[DeviceSnapshot]:
    """Load and normalise every snapshot, sorted by device then time."""
    snapshots = [normalize(record) for record in iter_raw_records(path)]
    snapshots.sort(key=lambda s: (s.device_id, s.collected_at))
    return snapshots
