"""Collect pytest node ids with human descriptions for the Eval UI.

Descriptions come from, in order:
1. YAML case ``question`` (live parametrized agent cases)
2. The test function docstring (first paragraph)
3. A humanized form of the test name
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = Path(__file__).resolve().parent / "cases"

_YAML_BY_PREFIX = {
    "test_grounded_qa": "qa_cases.yaml",
    "test_adversarial": "adversarial_cases.yaml",
    "test_action_proposals": "action_cases.yaml",
}


def _first_paragraph(doc: str | None) -> str | None:
    if not doc:
        return None
    text = doc.strip()
    if not text:
        return None
    # Drop leading blank / keep the first block.
    parts = re.split(r"\n\s*\n", text, maxsplit=1)
    line = " ".join(parts[0].split())
    return line or None


def _humanize(name: str) -> str:
    base = name.split("[", 1)[0]
    base = re.sub(r"^test_", "", base)
    return base.replace("_", " ").strip().capitalize()


def _param_id(node_name: str) -> str | None:
    if "[" in node_name and node_name.endswith("]"):
        return node_name[node_name.index("[") + 1 : -1]
    return None


def _load_yaml_case_map() -> dict[str, dict[str, Any]]:
    """Map ``test_fn[case_id]`` → case dict for live YAML suites."""
    out: dict[str, dict[str, Any]] = {}
    for prefix, filename in _YAML_BY_PREFIX.items():
        path = CASES_DIR / filename
        if not path.exists():
            continue
        rows = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        for row in rows:
            cid = row.get("id")
            if cid:
                out[f"{prefix}[{cid}]"] = row
    return out


def _docstrings_in_file(path: Path) -> dict[str, str]:
    """Map function name → first-paragraph docstring."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return {}
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test_"
        ):
            doc = _first_paragraph(ast.get_docstring(node))
            if doc:
                found[node.name] = doc
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and child.name.startswith("test_"):
                    doc = _first_paragraph(ast.get_docstring(child))
                    if doc:
                        found[f"{node.name}::{child.name}"] = doc
                        found[child.name] = doc
    return found


def _module_doc(path: Path) -> str | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return None
    return _first_paragraph(ast.get_docstring(tree))


def describe_nodeid(nodeid: str, yaml_cases: dict[str, dict[str, Any]] | None = None) -> str:
    """Return a short description for a pytest node id."""
    yaml_cases = yaml_cases if yaml_cases is not None else _load_yaml_case_map()
    if "::" not in nodeid:
        return _humanize(nodeid)

    file_part, name = nodeid.split("::", 1)
    # Class-based: file.py::Class::test_name
    if "::" in name:
        _, name = name.rsplit("::", 1)

    # Live YAML parametrized cases — prefer the operator question.
    yaml_row = yaml_cases.get(name)
    if yaml_row:
        question = (yaml_row.get("question") or "").strip()
        if question:
            company = yaml_row.get("company_id")
            if company:
                return f"{question} (tenant {company})"
            return question

    path = REPO_ROOT / file_part.replace("\\", "/")
    docs = _docstrings_in_file(path) if path.exists() else {}
    fn = name.split("[", 1)[0]
    if fn in docs:
        desc = docs[fn]
        param = _param_id(name)
        if param:
            return f"{desc} — case `{param}`"
        return desc

    # Fall back to module intent + humanized name.
    module = _module_doc(path) if path.exists() else None
    human = _humanize(name)
    if module and len(module) <= 140:
        return f"{human}. {module}"
    return human


def enrich_cases(
    nodeids: list[str],
    category: str,
    *,
    status: str = "pending",
) -> list[dict[str, Any]]:
    """Build catalog rows with descriptions for the Eval UI."""
    yaml_cases = _load_yaml_case_map()
    rows: list[dict[str, Any]] = []
    for nodeid in nodeids:
        name = nodeid.split("::")[-1] if "::" in nodeid else nodeid
        rows.append(
            {
                "id": nodeid,
                "name": name,
                "category": category,
                "status": status,
                "description": describe_nodeid(nodeid, yaml_cases),
                "message": None,
                "duration_s": None,
            }
        )
    return rows
