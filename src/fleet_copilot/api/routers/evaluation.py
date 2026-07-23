"""Evaluation scorecard API — run deterministic / live suites from the UI."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...services.evaluation import get_evaluation_service

router = APIRouter(tags=["evaluation"])

Tier = Literal["deterministic", "live", "both"]


class EvalStartRequest(BaseModel):
    tier: Tier = Field(
        default="deterministic",
        description="deterministic (free), live (needs OPENAI_API_KEY), or both",
    )


@router.get("/eval")
def eval_status() -> dict:
    """Latest scorecard plus whether a run is in progress."""
    return get_evaluation_service().status()


@router.post("/eval/run")
def eval_run(body: EvalStartRequest) -> dict:
    """Start an evaluation. Rejects if one is already running."""
    try:
        return get_evaluation_service().start(body.tier)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
