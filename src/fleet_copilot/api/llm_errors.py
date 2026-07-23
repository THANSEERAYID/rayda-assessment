"""Translate OpenAI client failures into clean HTTP responses.

Without this, a quota/auth/connectivity failure from the model provider bubbles
up as an unhandled exception — a 500 with a multi-frame stack trace shown to
whoever's driving the UI. None of these are bugs in this codebase; they're
upstream account/network state, and the caller needs a short, actionable
message instead of a traceback.
"""
from __future__ import annotations

import openai
from fastapi import HTTPException


def translate_llm_error(exc: Exception) -> HTTPException:
    if isinstance(exc, openai.RateLimitError):
        # OpenAI reuses HTTP 429 for both "too many requests" and "no quota
        # left" — only the error body's code actually distinguishes them.
        if "insufficient_quota" in str(exc):
            return HTTPException(
                status_code=503,
                detail=(
                    "The OpenAI account behind this API key has no funded quota. "
                    "This is a billing issue, not an application error — add "
                    "credit or a payment method at platform.openai.com/settings/organization/billing."
                ),
            )
        return HTTPException(
            status_code=503,
            detail="The model provider is rate-limiting this API key. Wait a moment and retry.",
        )
    if isinstance(exc, openai.AuthenticationError):
        return HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY was rejected by OpenAI. Check the key in .env.",
        )
    if isinstance(exc, openai.APIConnectionError | openai.APITimeoutError):
        return HTTPException(
            status_code=502,
            detail="Could not reach the model provider. Check network connectivity and retry.",
        )
    if isinstance(exc, openai.APIError):
        return HTTPException(status_code=502, detail=f"Model provider error: {exc}")
    raise exc
