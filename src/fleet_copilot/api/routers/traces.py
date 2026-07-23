"""Run trace and audit log endpoints — the traceability surface."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from ...storage.db import connect
from ...storage.repositories.audit import AuditRepository, RunTraceRepository
from ...storage.repositories.audit import ThreadRepository
from ...storage.repositories.evidence import EvidenceRepository
from ...storage.repositories.turns import TurnRepository
from ..deps import require_company
from ..schemas import AuditEventOut, AuditOut, TraceOut, TraceStepOut

router = APIRouter(tags=["traceability"])


@router.get("/evidence")
def get_evidence(company_id: str, ids: str) -> dict:
    """Resolve citation ids to the readings behind them.

    Lets a proposal's ``evidence_ids`` be opened long after the turn that made
    it — the Approvals queue holds proposals from threads whose in-memory ledger
    is long gone. Tenant-scoped, because an id on a proposal is a bare string
    and must not be a way to read another company's telemetry.
    """
    require_company(company_id)
    wanted = [part for part in (ids or "").split(",") if part.strip()]
    with connect() as conn:
        records = EvidenceRepository(conn).get_many(wanted, company_id=company_id)
    return {
        "company_id": company_id,
        "evidence": [r.model_dump(mode="json") for r in records],
    }


@router.get("/turns")
def get_turns(company_id: str, kind: str | None = None, limit: int = 100) -> dict:
    """Completed turns for a tenant, so their results survive a refresh.

    Used by the Action-performed view (``kind=task``) to reload investigations
    that were run earlier. The stored ``result`` is a snapshot; the caller
    overlays live proposal status from ``/actions`` so nothing shown goes stale.
    """
    require_company(company_id)
    with connect() as conn:
        rows = TurnRepository(conn).list_for_company(company_id, kind=kind, limit=limit)
    return {
        "company_id": company_id,
        "turns": [
            {
                "turn_id": r.turn_id,
                "thread_id": r.thread_id,
                "kind": r.kind,
                "question": r.question,
                "result": json.loads(r.result or "{}"),
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


@router.get("/traces", response_model=TraceOut)
def get_company_traces(company_id: str, limit_runs: int = 50) -> TraceOut:
    """Every run this tenant's agent has performed, newest first.

    The trace viewer is an audit surface, so it is scoped to the company rather
    than to one conversation — a per-thread view makes a short thread
    indistinguishable from a truncated trace, and hides everything the agent did
    in the other threads.
    """
    require_company(company_id)
    with connect() as conn:
        rows = RunTraceRepository(conn).list_steps_for_company(
            company_id, limit_runs=limit_runs
        )
    return TraceOut(company_id=company_id, steps=[_step(r) for r in rows])


def _step(row) -> TraceStepOut:
    return TraceStepOut(
        seq=row.seq,
        turn_id=row.turn_id,
        thread_id=getattr(row, "thread_id", None),
        node=row.node,
        status=row.status,
        detail=json.loads(row.detail or "{}"),
        duration_ms=row.duration_ms,
        created_at=row.created_at.isoformat(),
    )


@router.get("/threads/{thread_id}/trace", response_model=TraceOut)
def get_trace(
    thread_id: str, company_id: str, turn_id: str | None = None
) -> TraceOut:
    """Every node the agent ran for a thread, in order, with timings.

    This is what the trace viewer renders: planning decisions, each tool call and
    its arguments, grounding rejections, and the approval pause.

    ``company_id`` is required and checked against the thread's binding. A trace
    carries the question, the retrieved telemetry and the tool arguments, so a
    thread id alone must not be enough to read one — ids are guessable in a way
    tenancy is not.
    """
    require_company(company_id)
    with connect() as conn:
        bound = ThreadRepository(conn).company_for(thread_id)
        if bound is None:
            raise HTTPException(status_code=404, detail="Unknown conversation thread.")
        if bound != company_id:
            # Deliberately the same response as an unknown thread: confirming a
            # thread exists under another tenant is itself a disclosure.
            raise HTTPException(status_code=404, detail="Unknown conversation thread.")
        rows = RunTraceRepository(conn).list_steps(thread_id, turn_id)
    return TraceOut(
        thread_id=thread_id, company_id=company_id, steps=[_step(r) for r in rows]
    )


@router.get("/audit", response_model=AuditOut)
def get_audit(
    company_id: str, thread_id: str | None = None, limit: int = 200
) -> AuditOut:
    require_company(company_id)
    with connect() as conn:
        rows = AuditRepository(conn).list_events(
            company_id, thread_id=thread_id, limit=limit
        )
    return AuditOut(
        company_id=company_id,
        events=[
            AuditEventOut(
                id=r.id,
                event_type=r.event_type,
                actor=r.actor,
                summary=r.summary,
                detail=json.loads(r.detail or "{}"),
                thread_id=r.thread_id,
                created_at=r.created_at.isoformat(),
            )
            for r in rows
        ],
    )
