"""FastAPI application."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config import settings
from ..storage.db import create_schema, get_engine
from .routers import (
    actions,
    chat,
    emails,
    evaluation,
    insights,
    prompts,
    tenants,
    tickets,
    traces,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Operational tables must exist before the first request; telemetry is
    # loaded separately by `make ingest` so a restart never re-reads the dataset.
    create_schema(get_engine())
    yield


app = FastAPI(
    title="Fleet Copilot",
    version="0.1.0",
    description=(
        "Agentic copilot over device telemetry. Answers are grounded in cited "
        "evidence; state-changing actions require human approval."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (
    tenants,
    chat,
    actions,
    insights,
    prompts,
    tickets,
    emails,
    traces,
    evaluation,
):
    app.include_router(module.router, prefix="/api")


@app.get("/api/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "model": settings.openai_model,
        "llm_configured": bool(settings.openai_api_key),
    }
