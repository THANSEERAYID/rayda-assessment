"""Insight scan endpoint.

Findings come straight from the deterministic detectors, with no model in the
path, so this endpoint returns the same result every time for the same data.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ...services.insights.registry import available_detectors, run_scan
from ...services.insights.trends import build_fleet_trends
from ...storage.db import connect
from ..deps import require_company
from ..schemas import InsightsOut, InsightsTrendsOut

router = APIRouter(tags=["insights"])


@router.get("/insights", response_model=InsightsOut)
def get_insights(
    company_id: str,
    window_days: int = 30,
    detectors: str | None = None,
) -> InsightsOut:
    require_company(company_id)
    selected = [d.strip() for d in detectors.split(",")] if detectors else None
    with connect() as conn:
        try:
            output = run_scan(
                conn, company_id, detectors=selected, window_days=window_days
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return InsightsOut(
        company_id=company_id,
        window_days=window_days,
        findings=output.findings,
        detectors_available=available_detectors(),
    )


@router.get("/insights/trends", response_model=InsightsTrendsOut)
def get_insight_trends(
    company_id: str,
    window_days: int = 30,
    limit: int = 3,
) -> InsightsTrendsOut:
    """Standing trend lines for the dashboard (disk / RAM / battery pressure)."""
    require_company(company_id)
    limit = max(1, min(limit, 6))
    with connect() as conn:
        output = run_scan(conn, company_id, window_days=window_days)
        charts = build_fleet_trends(
            conn,
            company_id,
            output.findings,
            window_days=window_days,
            limit=limit,
        )
    return InsightsTrendsOut(
        company_id=company_id,
        window_days=window_days,
        charts=charts,
    )
