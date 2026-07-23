"""A completed turn's result must survive the turn, and stay tenant-scoped.

The Action-performed view reloads investigations from here after a refresh, so
the guarantees that matter are: a resume updates rather than duplicates, kinds
are separable (task vs chat), and one tenant's turns never surface for another.
No model calls.
"""
from __future__ import annotations

import pytest

from fleet_copilot.storage.repositories.turns import TurnRepository


def _result(answer: str = "Two devices are low on disk.") -> dict:
    return {"answer": answer, "claims": [], "pending_actions": []}


def test_a_turn_is_readable_after_it_finishes(conn):
    repo = TurnRepository(conn)
    repo.upsert(
        turn_id="turn-a",
        thread_id="thr-a",
        company_id="acme-001",
        kind="task",
        question="Which devices are low on disk?",
        result=_result(),
    )
    rows = repo.list_for_company("acme-001", kind="task")
    assert [r.turn_id for r in rows] == ["turn-a"]
    assert rows[0].question == "Which devices are low on disk?"


def test_resuming_updates_the_same_row(conn):
    """A resume keeps one row and refreshes its result — no duplicate."""
    repo = TurnRepository(conn)
    repo.upsert(
        turn_id="turn-b", thread_id="thr-b", company_id="acme-001",
        kind="task", question="q", result=_result("before approval"),
    )
    repo.upsert(
        turn_id="turn-b", thread_id="thr-b", company_id="acme-001",
        kind="task", question="q", result=_result("after approval"),
    )
    import json

    rows = repo.list_for_company("acme-001", kind="task")
    mine = [r for r in rows if r.turn_id == "turn-b"]
    assert len(mine) == 1
    assert json.loads(mine[0].result)["answer"] == "after approval"


def test_kind_separates_task_from_chat(conn):
    repo = TurnRepository(conn)
    repo.upsert(turn_id="turn-task", thread_id="t1", company_id="acme-001",
                kind="task", question="q1", result=_result())
    repo.upsert(turn_id="turn-chat", thread_id="t2", company_id="acme-001",
                kind="chat", question="q2", result=_result())
    task_ids = {r.turn_id for r in repo.list_for_company("acme-001", kind="task")}
    assert "turn-task" in task_ids
    assert "turn-chat" not in task_ids


def test_turns_do_not_cross_tenants(conn):
    repo = TurnRepository(conn)
    repo.upsert(turn_id="turn-acme", thread_id="t", company_id="acme-001",
                kind="task", question="secret", result=_result())
    other = {r.turn_id for r in repo.list_for_company("globex-002")}
    assert "turn-acme" not in other


def test_the_endpoint_is_tenant_scoped(engine):
    # Committed, because the endpoint opens its own connection and would not see
    # the conn fixture's open transaction.
    from fastapi import HTTPException
    from fleet_copilot.api.routers.traces import get_turns

    with engine.begin() as setup:
        TurnRepository(setup).upsert(
            turn_id="turn-ep", thread_id="t", company_id="acme-001",
            kind="task", question="q", result=_result(),
        )
    try:
        assert any(
            t["turn_id"] == "turn-ep"
            for t in get_turns("acme-001", kind="task")["turns"]
        )
        assert get_turns("globex-002", kind="task")["turns"] == []
        with pytest.raises(HTTPException):
            get_turns("not-a-company")
    finally:
        from sqlalchemy import text

        with engine.begin() as teardown:
            teardown.execute(text("delete from turns where turn_id='turn-ep'"))
