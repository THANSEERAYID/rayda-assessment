"""Model factory and the guarded call path.

Determinism is pursued as far as the API allows: temperature 0 and a fixed seed.
That is "reasonably deterministic", not deterministic — sampling at temperature 0
is still not a guarantee across model revisions. The evaluation suite is designed
around that limitation: its deterministic tier never calls a model at all, and
the live tier asserts on retrieved evidence and typed refusals rather than on
exact prose.

Every call goes through :func:`invoke_llm`, which applies the concurrency slot
and enforces the per-turn call ceiling. Nodes never call ``ainvoke`` directly, so
there is one place where a runaway turn can be stopped.

Token usage is captured here and stashed for the next :func:`record_step`, so the
run trace can show which model ran and what it spent without every node wiring
usage plumbing by hand.
"""
from __future__ import annotations

from contextvars import ContextVar
from functools import lru_cache
from typing import Any

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from ..config import settings
from .rate_limit import get_rate_limiter, llm_slot

# Last successful call's usage, consumed by the next record_step.
_LAST_LLM_USAGE: ContextVar[dict[str, Any] | None] = ContextVar(
    "fleet_last_llm_usage", default=None
)


class MissingAPIKey(RuntimeError):
    pass


class LLMBudgetExceeded(RuntimeError):
    """A turn hit its ceiling on model calls.

    Distinct from a loop bound inside one node: this counts every call the turn
    has made across planning, dispatch, all workers and grounding, so it catches
    a runaway that no single node's limit would.
    """


@lru_cache(maxsize=4)
def get_llm(temperature: float | None = None) -> ChatOpenAI:
    if not settings.openai_api_key:
        raise MissingAPIKey(
            "OPENAI_API_KEY is not set. The agent needs it; the deterministic "
            "evaluation tier (`make eval`) does not."
        )
    return ChatOpenAI(
        model=settings.openai_model,
        temperature=settings.openai_temperature if temperature is None else temperature,
        timeout=settings.openai_timeout_seconds,
        seed=settings.openai_seed,
        api_key=settings.openai_api_key,
        max_retries=2,
        # Paces requests against the provider's per-minute allowance so a
        # multi-call turn does not trip a 429 halfway through.
        rate_limiter=get_rate_limiter(),
    )


def check_budget(calls_so_far: int) -> None:
    """Raise before spending a call that would exceed the turn's ceiling."""
    if calls_so_far >= settings.max_llm_calls_per_turn:
        raise LLMBudgetExceeded(
            f"This turn reached its limit of {settings.max_llm_calls_per_turn} "
            "model calls and was stopped."
        )


def take_llm_usage() -> dict[str, Any] | None:
    """Return and clear usage from the most recent :func:`invoke_llm` call."""
    usage = _LAST_LLM_USAGE.get()
    _LAST_LLM_USAGE.set(None)
    return usage


async def invoke_llm(model: Any, messages: Any, *, calls_so_far: int = 0) -> Any:
    """Make one model call under the concurrency limit and the turn budget."""
    check_budget(calls_so_far)
    callback = UsageMetadataCallbackHandler()
    async with llm_slot():
        result = await model.ainvoke(messages, config={"callbacks": [callback]})
    _LAST_LLM_USAGE.set(_extract_usage(callback.usage_metadata, result))
    return result


def _extract_usage(
    callback_usage: dict[str, Any] | None, result: Any
) -> dict[str, Any] | None:
    """Normalise provider usage into a small dict for the run trace."""
    if callback_usage:
        # {model_name: UsageMetadata}
        model_name, meta = next(iter(callback_usage.items()))
        return _usage_dict(
            model=str(model_name),
            prompt_tokens=_token(meta, "input_tokens", "prompt_tokens"),
            completion_tokens=_token(meta, "output_tokens", "completion_tokens"),
            total_tokens=_token(meta, "total_tokens"),
        )

    if isinstance(result, AIMessage):
        meta = result.usage_metadata or {}
        model_name = (
            (result.response_metadata or {}).get("model_name")
            or (result.response_metadata or {}).get("model")
            or settings.openai_model
        )
        if meta or model_name:
            return _usage_dict(
                model=str(model_name),
                prompt_tokens=_token(meta, "input_tokens", "prompt_tokens"),
                completion_tokens=_token(meta, "output_tokens", "completion_tokens"),
                total_tokens=_token(meta, "total_tokens"),
            )

    # Structured-output calls still spent tokens even when the callback missed
    # them — at least record which model was configured.
    return _usage_dict(model=settings.openai_model)


def _token(meta: Any, *keys: str) -> int | None:
    if not meta:
        return None
    if isinstance(meta, dict):
        for key in keys:
            value = meta.get(key)
            if value is not None:
                return int(value)
        return None
    for key in keys:
        value = getattr(meta, key, None)
        if value is not None:
            return int(value)
    return None


def _usage_dict(
    *,
    model: str,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
) -> dict[str, Any]:
    if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens
    return {
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def llm_available() -> bool:
    return bool(settings.openai_api_key)
