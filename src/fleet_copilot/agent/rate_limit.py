"""Rate limiting for model calls.

Two distinct problems, handled separately because they fail differently:

*Throughput* — a token bucket paces requests against the provider's per-minute
allowance. Without it a single multi-agent turn can fire a dozen calls back to
back and trip a 429 partway through, wasting the calls already spent.

*Concurrency* — a semaphore bounds how many calls are in flight process-wide.
The bucket alone does not do this: several turns arriving together each draw
their own tokens and can still open many simultaneous connections.

Both are process-local, which is the honest scope for a single-process
deployment. A multi-replica deployment would need a shared limiter (Redis or
the provider's own headers); the seam for that is :func:`llm_slot`.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import AsyncIterator

from langchain_core.rate_limiters import InMemoryRateLimiter

from ..config import settings


@lru_cache(maxsize=1)
def get_rate_limiter() -> InMemoryRateLimiter:
    """Shared token bucket, attached to every model instance.

    ``max_bucket_size`` allows a small burst — a turn's opening planning and
    dispatch calls should not each wait a full interval — while the refill rate
    holds the sustained average.
    """
    return InMemoryRateLimiter(
        requests_per_second=settings.llm_requests_per_second,
        check_every_n_seconds=0.05,
        max_bucket_size=settings.llm_max_bucket_size,
    )


@lru_cache(maxsize=1)
def _semaphore() -> asyncio.Semaphore:
    return asyncio.Semaphore(settings.llm_max_concurrency)


@asynccontextmanager
async def llm_slot() -> AsyncIterator[None]:
    """Hold one of the available concurrent model slots."""
    async with _semaphore():
        yield


def reset_limiters() -> None:
    """Drop the cached limiter and semaphore.

    Needed by tests that change the settings, and after an event loop is
    replaced — a semaphore is bound to the loop it was created on.
    """
    get_rate_limiter.cache_clear()
    _semaphore.cache_clear()
