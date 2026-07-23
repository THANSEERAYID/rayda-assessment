"""Structured evaluation scorecard — CLI and Eval UI share this runner.

Runs pytest in a *subprocess* so a live/deterministic suite never rebinds the
API process's database engine (the suite seeds its own temp SQLite).

Progress for the Eval UI is emitted as ``@@EVAL@@{json}`` lines so the parent
process can update a live case listing as each test finishes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

REPO_ROOT = Path(__file__).resolve().parents[1]

Tier = Literal["deterministic", "live", "both"]
PROGRESS_PREFIX = "@@EVAL@@"

# Grouped by the property being proven rather than by file.
DETERMINISTIC_CATEGORIES: list[tuple[str, str, str]] = [
    (
        "Retrieval correctness",
        "eval/deterministic/test_repositories.py",
        "as-of semantics, tenant scoping, raw-record fidelity",
    ),
    (
        "Insight correctness",
        "eval/deterministic/test_insights.py",
        "detectors vs independently computed ground truth",
    ),
    (
        "Tool contract (over MCP)",
        "eval/deterministic/test_mcp_tools.py",
        "the brief's example questions, asked of the tools directly",
    ),
    (
        "Tenant isolation",
        "eval/deterministic/test_tenant_isolation.py",
        "cross-tenant reads and writes refused and audited",
    ),
    (
        "MCP prompt catalogue",
        "eval/deterministic/test_mcp_prompts.py",
        "workflows advertised, arguments declared, every template renders",
    ),
    (
        "Action guardrails",
        "eval/deterministic/test_action_state_machine.py",
        "no execution without human approval",
    ),
    (
        "Grounding enforcement",
        "eval/deterministic/test_evidence_validator.py",
        "fabricated citations and invented figures rejected",
    ),
    (
        "Prompt injection defence",
        "eval/deterministic/test_prompt_injection.py",
        "hostile telemetry cannot break out of its field in a prompt",
    ),
    (
        "Agent capability bounds",
        "eval/deterministic/test_worker_scoping.py",
        "action agent cannot discover or cite; dispatch repaired in code",
    ),
    (
        "Worker handoff",
        "eval/deterministic/test_handoff.py",
        "one agent's evidence reaches the next agent's prompt",
    ),
    (
        "Graph state schema",
        "eval/deterministic/test_state_schema.py",
        "typed state; misspelled fields rejected on read and on write",
    ),
    (
        "Tool payload validation",
        "eval/deterministic/test_tool_result_ingestion.py",
        "malformed tool results fail at the call that produced them",
    ),
    (
        "Rate limits & loop breaking",
        "eval/deterministic/test_rate_limiting.py",
        "throughput paced, concurrency capped, runaway loops stopped early",
    ),
    (
        "Reviewer signals (HITL)",
        "eval/deterministic/test_review_signals.py",
        "objective proposal/answer quality, never a model self-assessment",
    ),
    (
        "Chart grounding",
        "eval/deterministic/test_chart_builder.py",
        "charts resolve from retrieved data or are dropped",
    ),
]

LIVE_CATEGORY = (
    "Live agent (structural)",
    "eval/live",
    "end-to-end model turns — citations, refusals, approvals; never wording",
)

_OUTCOME_RE = re.compile(
    r"^(?P<nodeid>\S+::\S+)\s+(?P<outcome>PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit(event: dict[str, Any]) -> None:
    """Machine-readable progress for the Eval UI parent process."""
    print(PROGRESS_PREFIX + json.dumps(event, ensure_ascii=True), flush=True)


def _case_from_nodeid(
    nodeid: str,
    category: str,
    status: str = "pending",
    description: str | None = None,
) -> dict[str, Any]:
    name = nodeid.split("::")[-1] if "::" in nodeid else nodeid
    # Local import keeps the CLI usable even if yaml is oddly missing.
    if description is None:
        try:
            from case_descriptions import describe_nodeid

            description = describe_nodeid(nodeid)
        except Exception:  # noqa: BLE001
            description = name.replace("_", " ")
    return {
        "id": nodeid,
        "name": name,
        "category": category,
        "status": status,
        "description": description,
        "message": None,
        "duration_s": None,
    }


def _pytest_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", str(REPO_ROOT / "src"))
    env["PY_COLORS"] = "0"
    env["NO_COLOR"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _collect_nodeids(target: str, *, live: bool = False) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        target,
        "--collect-only",
        "-q",
        "--no-header",
    ]
    if live:
        cmd.append("--live")
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=_pytest_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    nodeids: list[str] = []
    for line in (proc.stdout or "").splitlines():
        text = line.strip()
        if "::" in text and not text.startswith("="):
            # Drop trailing "N tests collected" style noise.
            if text[0].isdigit():
                continue
            nodeids.append(text.split()[0])
    return nodeids


def _parse_junit(path: Path, category: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    root = ET.parse(path).getroot()
    cases: list[dict[str, Any]] = []
    for suite in root.iter("testsuite"):
        for case in suite.findall("testcase"):
            name = case.attrib.get("name", "unknown")
            classname = case.attrib.get("classname", "")
            file_attr = case.attrib.get("file") or ""
            duration = float(case.attrib.get("time") or 0)
            status = "passed"
            message: str | None = None
            if case.find("skipped") is not None:
                status = "skipped"
                message = (case.find("skipped").attrib.get("message") or "")[:500]
            elif case.find("failure") is not None:
                status = "failed"
                fail = case.find("failure")
                message = ((fail.attrib.get("message") or "") + "\n" + (fail.text or ""))[
                    :800
                ].strip()
            elif case.find("error") is not None:
                status = "error"
                err = case.find("error")
                message = ((err.attrib.get("message") or "") + "\n" + (err.text or ""))[
                    :800
                ].strip()
            # Prefer a pytest-style nodeid when the file path is present.
            if file_attr:
                nodeid = f"{file_attr.replace(chr(92), '/')}::{name}"
            elif classname:
                nodeid = f"{classname}::{name}"
            else:
                nodeid = name
            cases.append(
                {
                    "id": nodeid,
                    "name": name,
                    "category": category,
                    "status": status,
                    "message": message or None,
                    "duration_s": round(duration, 3),
                }
            )
    return cases


def _merge_junit_messages(
    live_cases: list[dict[str, Any]],
    junit_cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_name = {c["name"]: c for c in junit_cases}
    merged: list[dict[str, Any]] = []
    for case in live_cases:
        j = by_name.get(case["name"])
        if j:
            merged.append(
                {
                    **case,
                    "status": j["status"],
                    "message": j.get("message") or case.get("message"),
                    "description": case.get("description") or j.get("description"),
                    "duration_s": (
                        j["duration_s"]
                        if j.get("duration_s") is not None
                        else case.get("duration_s")
                    ),
                }
            )
        else:
            merged.append(case)
    # Any junit-only cases (collection edge) still count.
    seen = {c["name"] for c in merged}
    for j in junit_cases:
        if j["name"] not in seen:
            merged.append(j)
    return merged


def _run_pytest(
    target: str,
    category: str,
    *,
    live: bool = False,
    on_line: Callable[[str], None] | None = None,
) -> tuple[list[dict[str, Any]], str, int]:
    """Collect cases, run with -v, stream per-case outcomes, return final list."""
    nodeids = _collect_nodeids(target, live=live)
    try:
        from case_descriptions import enrich_cases

        pending = enrich_cases(nodeids, category, status="pending")
    except Exception:  # noqa: BLE001
        pending = [_case_from_nodeid(n, category, "pending") for n in nodeids]
    _emit({"type": "catalog", "category": category, "cases": pending})

    if pending:
        first = {**pending[0], "status": "running"}
        _emit({"type": "case", "case": first})

    with tempfile.TemporaryDirectory(prefix="fleet-eval-") as tmp:
        junit = Path(tmp) / "junit.xml"
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            target,
            "-v",
            "--no-header",
            f"--junitxml={junit}",
        ]
        if live:
            cmd.append("--live")

        proc = subprocess.Popen(
            cmd,
            cwd=REPO_ROOT,
            env=_pytest_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        lines: list[str] = []
        assert proc.stdout is not None
        # Track order so we can mark the next pending case as running.
        remaining = [c["id"] for c in pending]
        live_by_id = {c["id"]: dict(c) for c in pending}
        if remaining:
            live_by_id[remaining[0]]["status"] = "running"

        for raw in proc.stdout:
            line = raw.rstrip("\n")
            lines.append(line)
            if on_line is not None:
                on_line(line)

            match = _OUTCOME_RE.match(line.strip())
            if not match:
                continue
            nodeid = match.group("nodeid")
            outcome = match.group("outcome").lower()
            if outcome == "xfail":
                status = "skipped"
            elif outcome == "xpass":
                status = "passed"
            else:
                status = outcome

            case = live_by_id.get(nodeid) or _case_from_nodeid(nodeid, category)
            case = {
                **case,
                "status": status,
                "category": category,
                "description": case.get("description")
                or _case_from_nodeid(nodeid, category).get("description"),
            }
            live_by_id[nodeid] = case
            _emit({"type": "case", "case": case})

            if nodeid in remaining:
                remaining.remove(nodeid)
            if remaining:
                nxt = live_by_id.get(remaining[0])
                if nxt and nxt.get("status") == "pending":
                    nxt = {**nxt, "status": "running"}
                    live_by_id[remaining[0]] = nxt
                    _emit({"type": "case", "case": nxt})

        proc.wait()
        output = "\n".join(lines)
        ordered = (
            [live_by_id[n] for n in nodeids if n in live_by_id]
            if nodeids
            else list(live_by_id.values())
        )
        junit_cases = _parse_junit(junit, category)
        cases = _merge_junit_messages(ordered, junit_cases) if ordered else junit_cases
        # Push final statuses (with failure messages) to the UI.
        for case in cases:
            _emit({"type": "case", "case": case})
        return cases, output, proc.returncode


def _summarise_category(
    name: str,
    description: str,
    tier: str,
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    passed = sum(1 for c in cases if c["status"] == "passed")
    failed = sum(1 for c in cases if c["status"] in ("failed", "error"))
    skipped = sum(1 for c in cases if c["status"] == "skipped")
    return {
        "name": name,
        "description": description,
        "tier": tier,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "cases": cases,
    }


def run_scorecard(
    tier: Tier = "deterministic",
    *,
    on_line: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Build a full scorecard dict for the requested tier."""
    llm_configured = bool(os.environ.get("OPENAI_API_KEY"))
    report: dict[str, Any] = {
        "run_id": str(uuid.uuid4()),
        "tier": tier,
        "status": "completed",
        "started_at": _utc_now(),
        "finished_at": None,
        "llm_configured": llm_configured,
        "total_passed": 0,
        "total_failed": 0,
        "total_skipped": 0,
        "categories": [],
        "cases": [],
        "error": None,
        "log_tail": [],
    }

    log_tail: list[str] = []
    all_cases: list[dict[str, Any]] = []

    def _capture(line: str) -> None:
        # Progress events are for the parent; keep the human log readable.
        if line.startswith(PROGRESS_PREFIX):
            if on_line is not None:
                on_line(line)
            return
        print(line, flush=True)
        log_tail.append(line)
        if len(log_tail) > 80:
            del log_tail[:-80]
        if on_line is not None:
            on_line(line)

    try:
        if tier in ("deterministic", "both"):
            for name, target, description in DETERMINISTIC_CATEGORIES:
                _capture(f"-> {name}")
                _emit({"type": "category_start", "category": name, "description": description})
                cases, output, code = _run_pytest(
                    target, name, live=False, on_line=_capture
                )
                if not cases and code != 0:
                    cases = [
                        {
                            "id": f"{name}::suite",
                            "name": "suite",
                            "category": name,
                            "status": "error",
                            "message": output[-800:] or f"pytest exited {code}",
                            "duration_s": None,
                        }
                    ]
                    _emit({"type": "case", "case": cases[0]})
                cat = _summarise_category(name, description, "deterministic", cases)
                report["categories"].append(cat)
                all_cases.extend(cases)
                _emit(
                    {
                        "type": "category_done",
                        "category": name,
                        "passed": cat["passed"],
                        "failed": cat["failed"],
                        "skipped": cat["skipped"],
                    }
                )

        if tier in ("live", "both"):
            if not llm_configured:
                report["status"] = "failed"
                report["error"] = (
                    "OPENAI_API_KEY is not set. Live agent eval needs a real model."
                )
            else:
                name, target, description = LIVE_CATEGORY
                _capture(f"-> {name} (this makes real model calls)")
                _emit({"type": "category_start", "category": name, "description": description})
                cases, output, code = _run_pytest(
                    target, name, live=True, on_line=_capture
                )
                if not cases and code != 0:
                    cases = [
                        {
                            "id": f"{name}::suite",
                            "name": "suite",
                            "category": name,
                            "status": "error",
                            "message": output[-800:] or f"pytest exited {code}",
                            "duration_s": None,
                        }
                    ]
                    _emit({"type": "case", "case": cases[0]})
                cat = _summarise_category(name, description, "live", cases)
                report["categories"].append(cat)
                all_cases.extend(cases)
                _emit(
                    {
                        "type": "category_done",
                        "category": name,
                        "passed": cat["passed"],
                        "failed": cat["failed"],
                        "skipped": cat["skipped"],
                    }
                )
                if not cases and report["error"] is None:
                    report["status"] = "failed"
                    report["error"] = "Live suite produced no test results."

    except Exception as exc:  # noqa: BLE001 — surface to the UI
        report["status"] = "failed"
        report["error"] = str(exc)

    report["finished_at"] = _utc_now()
    report["log_tail"] = log_tail[-40:]
    report["cases"] = all_cases
    report["total_passed"] = sum(c["passed"] for c in report["categories"])
    report["total_failed"] = sum(c["failed"] for c in report["categories"])
    report["total_skipped"] = sum(c["skipped"] for c in report["categories"])
    return report


def print_human(report: dict[str, Any]) -> int:
    print("\nFleet Copilot - evaluation scorecard")
    if report["tier"] == "deterministic":
        print("(no model calls; reproducible on a fresh clone)\n")
    elif report["tier"] == "live":
        print("(live agent tier — real model calls)\n")
    else:
        print("(deterministic + live)\n")

    if report.get("error"):
        print(f"  ERROR: {report['error']}\n")

    rows = report["categories"]
    if not rows:
        print("  No categories ran.\n")
        return 1

    width = max(len(r["name"]) for r in rows)
    for row in rows:
        mark = "PASS" if not row["failed"] else "FAIL"
        print(
            f"  [{mark}] {row['name']:<{width}}  "
            f"{row['passed']:>3} passed  {row['failed']:>2} failed"
        )
        print(f"         {'':<{width}}  {row['description']}")

    print(
        f"\n  Total: {report['total_passed']} passed, "
        f"{report['total_failed']} failed, "
        f"{report['total_skipped']} skipped\n"
    )
    return 1 if report["total_failed"] or report["status"] == "failed" else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fleet Copilot evaluation scorecard")
    parser.add_argument(
        "--tier",
        choices=["deterministic", "live", "both"],
        default="deterministic",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        help="Write the structured report to this path",
    )
    parser.add_argument(
        "--json-stdout",
        action="store_true",
        help="Print JSON instead of the human scorecard",
    )
    args = parser.parse_args(argv)

    report = run_scorecard(args.tier)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.json_stdout:
        print(json.dumps(report))
        return 1 if report["total_failed"] or report["status"] == "failed" else 0
    return print_human(report)


if __name__ == "__main__":
    raise SystemExit(main())
