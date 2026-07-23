"""Background evaluation jobs for the Eval UI.

The suite always runs in a subprocess via ``eval/scorecard.py`` so the API's
database engine is never rebound to the suite's temporary SQLite.

``@@EVAL@@`` progress lines update a flat case listing while the run is live.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from ..config import REPO_ROOT, settings

Tier = Literal["deterministic", "live", "both"]

RESULT_PATH = REPO_ROOT / "data" / "eval" / "latest.json"
PROGRESS_PREFIX = "@@EVAL@@"


def _describe_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill missing descriptions so the Eval UI always has something to show."""
    if not cases:
        return cases
    eval_dir = str(REPO_ROOT / "eval")
    if eval_dir not in sys.path:
        sys.path.insert(0, eval_dir)
    try:
        from case_descriptions import describe_nodeid
    except Exception:  # noqa: BLE001
        describe_nodeid = None  # type: ignore[assignment]

    enriched: list[dict[str, Any]] = []
    for case in cases:
        row = dict(case)
        if not (row.get("description") or "").strip():
            nodeid = row.get("id") or row.get("name") or ""
            if describe_nodeid is not None and nodeid:
                try:
                    row["description"] = describe_nodeid(str(nodeid))
                except Exception:  # noqa: BLE001
                    row["description"] = _humanize_name(str(row.get("name") or nodeid))
            else:
                row["description"] = _humanize_name(str(row.get("name") or nodeid))
        enriched.append(row)
    return enriched


def _humanize_name(name: str) -> str:
    base = name.split("[", 1)[0]
    if base.startswith("test_"):
        base = base[5:]
    return base.replace("_", " ").strip().capitalize() or name


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_state() -> dict[str, Any]:
    return {
        "run_id": None,
        "tier": None,
        "status": "idle",
        "started_at": None,
        "finished_at": None,
        "llm_configured": bool(settings.openai_api_key),
        "total_passed": 0,
        "total_failed": 0,
        "total_skipped": 0,
        "total_pending": 0,
        "categories": [],
        "cases": [],
        "error": None,
        "log_tail": [],
    }


def _recount(cases: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total_passed": sum(1 for c in cases if c.get("status") == "passed"),
        "total_failed": sum(
            1 for c in cases if c.get("status") in ("failed", "error")
        ),
        "total_skipped": sum(1 for c in cases if c.get("status") == "skipped"),
        "total_pending": sum(
            1 for c in cases if c.get("status") in ("pending", "running")
        ),
    }


class EvaluationService:
    """Singleton-style job manager — one eval at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] = self._load_or_empty()

    def _load_or_empty(self) -> dict[str, Any]:
        if RESULT_PATH.exists():
            try:
                data = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("status") == "running":
                    data["status"] = "failed"
                    data["error"] = data.get("error") or "Eval interrupted by a restart."
                    data["finished_at"] = data.get("finished_at") or _utc_now()
                    # Freeze in-flight cases so the listing does not look stuck.
                    cases = []
                    for case in data.get("cases") or []:
                        if case.get("status") in ("pending", "running"):
                            cases.append({**case, "status": "error", "message": "Interrupted"})
                        else:
                            cases.append(case)
                    data["cases"] = cases
                    data.update(_recount(cases))
                data["llm_configured"] = bool(settings.openai_api_key)
                data.setdefault("cases", [])
                data.setdefault("total_pending", 0)
                return data
            except (OSError, json.JSONDecodeError):
                pass
        return _empty_state()

    def _persist(self) -> None:
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.write_text(json.dumps(self._state, indent=2), encoding="utf-8")

    def status(self) -> dict[str, Any]:
        with self._lock:
            snap = dict(self._state)
            snap["llm_configured"] = bool(settings.openai_api_key)
            snap["cases"] = _describe_cases(list(snap.get("cases") or []))
            # Keep category nested cases in sync for any consumer that reads them.
            cats = []
            for cat in snap.get("categories") or []:
                c = dict(cat)
                c["cases"] = _describe_cases(list(c.get("cases") or []))
                cats.append(c)
            snap["categories"] = cats
            return snap

    def start(self, tier: Tier) -> dict[str, Any]:
        with self._lock:
            if self._state.get("status") == "running":
                return dict(self._state)

            if tier in ("live", "both") and not settings.openai_api_key:
                raise ValueError(
                    "OPENAI_API_KEY is not set. Add it to .env to run the live agent suite."
                )

            run_id = str(uuid.uuid4())
            self._state = {
                "run_id": run_id,
                "tier": tier,
                "status": "running",
                "started_at": _utc_now(),
                "finished_at": None,
                "llm_configured": bool(settings.openai_api_key),
                "total_passed": 0,
                "total_failed": 0,
                "total_skipped": 0,
                "total_pending": 0,
                "categories": [],
                "cases": [],
                "error": None,
                "log_tail": [f"Starting {tier} evaluation..."],
            }
            self._persist()
            self._thread = threading.Thread(
                target=self._run,
                args=(run_id, tier),
                name=f"eval-{tier}",
                daemon=True,
            )
            self._thread.start()
            return dict(self._state)

    def _apply_progress(self, run_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            if self._state.get("run_id") != run_id:
                return
            etype = event.get("type")
            cases: list[dict[str, Any]] = list(self._state.get("cases") or [])

            if etype == "catalog":
                category = event.get("category") or ""
                incoming = _describe_cases(list(event.get("cases") or []))
                # Replace this category's rows; keep earlier categories intact.
                cases = [c for c in cases if c.get("category") != category]
                cases.extend(incoming)
                # Category stub so the scorecard section can grow live.
                cats = list(self._state.get("categories") or [])
                if not any(c.get("name") == category for c in cats):
                    cats.append(
                        {
                            "name": category,
                            "description": "",
                            "tier": "live"
                            if "Live" in category
                            else "deterministic",
                            "passed": 0,
                            "failed": 0,
                            "skipped": 0,
                            "cases": [],
                        }
                    )
                    self._state["categories"] = cats

            elif etype == "case":
                case = event.get("case") or {}
                case_id = case.get("id")
                if not case_id:
                    return
                replaced = False
                for i, existing in enumerate(cases):
                    if existing.get("id") == case_id:
                        # Keep an earlier description if the update omits one.
                        merged = {**existing, **case}
                        if not merged.get("description"):
                            merged["description"] = existing.get("description")
                        cases[i] = merged
                        replaced = True
                        break
                if not replaced:
                    cases.append(case)

            elif etype == "category_start":
                category = event.get("category") or ""
                cats = list(self._state.get("categories") or [])
                if not any(c.get("name") == category for c in cats):
                    cats.append(
                        {
                            "name": category,
                            "description": event.get("description") or "",
                            "tier": "live"
                            if "Live" in category
                            else "deterministic",
                            "passed": 0,
                            "failed": 0,
                            "skipped": 0,
                            "cases": [],
                        }
                    )
                    self._state["categories"] = cats
                else:
                    for cat in cats:
                        if cat.get("name") == category and event.get("description"):
                            cat["description"] = event["description"]
                    self._state["categories"] = cats

            elif etype == "category_done":
                category = event.get("category")
                cats = list(self._state.get("categories") or [])
                for cat in cats:
                    if cat.get("name") == category:
                        cat["passed"] = int(event.get("passed") or 0)
                        cat["failed"] = int(event.get("failed") or 0)
                        cat["skipped"] = int(event.get("skipped") or 0)
                        cat["cases"] = [c for c in cases if c.get("category") == category]
                self._state["categories"] = cats

            self._state["cases"] = cases
            self._state.update(_recount(cases))
            self._persist()

    def _run(self, run_id: str, tier: Tier) -> None:
        script = REPO_ROOT / "eval" / "scorecard.py"
        out_path = REPO_ROOT / "data" / "eval" / f"{run_id}.json"
        cmd = [
            sys.executable,
            str(script),
            "--tier",
            tier,
            "--json-out",
            str(out_path),
        ]
        env = dict(os.environ)
        if settings.openai_api_key:
            env["OPENAI_API_KEY"] = settings.openai_api_key
        env.setdefault("OPENAI_MODEL", settings.openai_model)
        env.setdefault("PYTHONPATH", str(REPO_ROOT / "src"))
        env["PY_COLORS"] = "0"
        env["NO_COLOR"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=REPO_ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                text = line.rstrip("\n")
                if not text:
                    continue
                if text.startswith(PROGRESS_PREFIX):
                    try:
                        event = json.loads(text[len(PROGRESS_PREFIX) :])
                    except json.JSONDecodeError:
                        continue
                    self._apply_progress(run_id, event)
                    continue
                with self._lock:
                    if self._state.get("run_id") != run_id:
                        return
                    tail = list(self._state.get("log_tail") or [])
                    tail.append(text)
                    self._state["log_tail"] = tail[-60:]
                    self._persist()
            proc.wait()

            if out_path.exists():
                report = json.loads(out_path.read_text(encoding="utf-8"))
            else:
                report = {
                    "run_id": run_id,
                    "tier": tier,
                    "status": "failed",
                    "error": f"Scorecard exited with code {proc.returncode} and wrote no report.",
                    "categories": [],
                    "cases": [],
                    "total_passed": 0,
                    "total_failed": 0,
                    "total_skipped": 0,
                    "log_tail": [],
                }

            with self._lock:
                if self._state.get("run_id") != run_id:
                    return
                # Prefer the final report's cases, but keep live listing if empty.
                if not report.get("cases"):
                    report["cases"] = self._state.get("cases") or []
                report["cases"] = _describe_cases(list(report.get("cases") or []))
                report["run_id"] = run_id
                report["llm_configured"] = bool(settings.openai_api_key)
                if not report.get("log_tail"):
                    report["log_tail"] = self._state.get("log_tail") or []
                report.update(_recount(report.get("cases") or []))
                # Enrich nested category cases too.
                cats = []
                for cat in report.get("categories") or []:
                    c = dict(cat)
                    c["cases"] = _describe_cases(list(c.get("cases") or []))
                    cats.append(c)
                report["categories"] = cats
                self._state = report
                if self._state.get("status") == "running":
                    self._state["status"] = "completed"
                self._state["finished_at"] = self._state.get("finished_at") or _utc_now()
                self._persist()

            try:
                out_path.unlink(missing_ok=True)
            except OSError:
                pass

        except Exception as exc:  # noqa: BLE001
            with self._lock:
                if self._state.get("run_id") != run_id:
                    return
                self._state["status"] = "failed"
                self._state["error"] = str(exc)
                self._state["finished_at"] = _utc_now()
                self._persist()


_service: EvaluationService | None = None
_service_lock = threading.Lock()


def get_evaluation_service() -> EvaluationService:
    global _service
    with _service_lock:
        if _service is None:
            _service = EvaluationService()
        return _service
