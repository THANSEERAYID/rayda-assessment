"""Run the deterministic tier and print a category-by-category scorecard.

Thin wrapper around ``eval.scorecard`` so ``make scorecard`` keeps working.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow `python eval/report.py` without installing the eval package.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scorecard import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["--tier", "deterministic"]))
