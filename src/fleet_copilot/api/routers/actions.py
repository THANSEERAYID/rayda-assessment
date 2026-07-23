"""Approval endpoints — the human half of the human-in-the-loop gate."""
from __future__ import annotations

import openai
from fastapi import APIRouter, HTTPException

from ...agent.runtime import ApprovalExpired, TurnTimeout, resume_turn
from ...domain.enums import ActionStatus
from ...domain.errors import TenantViolation
from ...domain.models import ActionDecision, CopilotResponse
from ...services.actions import ActionService
from ...storage.db import connect
from ...storage.repositories.actions import ActionRepository, IllegalTransition
from ..deps import require_company
from ..llm_errors import translate_llm_error
from ..schemas import ApprovalIn, PendingActionsOut, TurnOut

router = APIRouter(tags=["actions"])


@router.get("/actions", response_model=PendingActionsOut)
def list_actions(company_id: str, status: str | None = None) -> PendingActionsOut:
    """List a company's actions, defaulting to those awaiting a decision."""
    require_company(company_id)
    wanted = ActionStatus(status) if status else ActionStatus.PROPOSED
    with connect() as conn:
        actions = ActionRepository(conn).list_by_status(company_id, wanted)
    return PendingActionsOut(company_id=company_id, actions=actions)


@router.post("/actions/decide", response_model=TurnOut)
async def decide(payload: ApprovalIn) -> TurnOut:
    """Approve or reject the actions a turn proposed, then finish the turn.

    Prefer resuming the suspended graph so the conversation can finish. If that
    pause is gone (process restart with an in-memory checkpointer, or an
    orphaned proposal), still apply the decisions against the action rows —
    otherwise the Approvals queue would accept clicks that change nothing.
    """
    require_company(payload.company_id)
    if not payload.decisions:
        raise HTTPException(status_code=400, detail="No decisions supplied.")

    decisions = [
        ActionDecision(action_id=d.action_id, approved=d.approved, note=d.note)
        for d in payload.decisions
    ]
    try:
        result = await resume_turn(payload.thread_id, payload.company_id, decisions)
    except TenantViolation as exc:
        raise HTTPException(status_code=403, detail=exc.message) from exc
    except ApprovalExpired:
        # Checkpoint gone (API reload) — still apply against action rows. Map
        # lifecycle conflicts here: an except-handler raise is not caught by
        # sibling ``except IllegalTransition`` on the outer try.
        try:
            result = _decide_without_resume(
                payload.thread_id, payload.company_id, decisions
            )
        except IllegalTransition as exc:
            raise HTTPException(
                status_code=409,
                detail=f"That action has already been decided. {exc}",
            ) from exc
    except TurnTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except IllegalTransition as exc:
        # Most often a double-click on Approve: the action was already decided.
        # A conflict, not a server fault — and the lifecycle correctly refused
        # to decide it twice.
        raise HTTPException(
            status_code=409,
            detail=f"That action has already been decided. {exc}",
        ) from exc
    except openai.APIError as exc:
        raise translate_llm_error(exc) from exc
    return TurnOut(**result.model_dump())


def _decide_without_resume(
    thread_id: str,
    company_id: str,
    decisions: list[ActionDecision],
) -> CopilotResponse:
    """Apply approvals when the conversation is no longer paused at the gate.

    The action rows are still authoritative for the Approvals queue. Carrying
    them out here keeps Approve/Reject honest when the checkpointer no longer
    has an interrupt to resume.
    """
    with connect() as conn:
        results = ActionService(conn, company_id, thread_id).apply_decisions(
            decisions, decided_by="it-admin"
        )

    if not results:
        raise HTTPException(
            status_code=409,
            detail=(
                "That approval request is no longer active and no matching "
                "proposed actions were found. Ask again to get a fresh "
                "proposal. Nothing was carried out."
            ),
        )

    executed = sum(1 for r in results if r.status is ActionStatus.EXECUTED)
    rejected = sum(1 for r in results if r.status is ActionStatus.REJECTED)
    parts: list[str] = []
    if executed:
        parts.append(f"executed {executed}")
    if rejected:
        parts.append(f"rejected {rejected}")
    summary = " and ".join(parts) if parts else f"updated {len(results)}"

    return CopilotResponse(
        thread_id=thread_id,
        company_id=company_id,
        answer=(
            f"Applied your decision from the approvals queue ({summary}). "
            "The original conversation was no longer waiting on a decision, "
            "so only the action lifecycle was updated."
        ),
        pending_actions=results,
        awaiting_approval=False,
    )
