"""Exponential backoff with jitter, applied to a streaming request."""
from __future__ import annotations

import asyncio
import random
from contextlib import aclosing
from typing import AsyncIterator, Awaitable, Callable

from .errors import OmniRouteError
from .events import RetryEvent, StreamEvent

MAX_RETRY_AFTER = 60.0  # never obey a server asking us to wait longer than this


def backoff_delay(attempt: int, *, base: float = 1.0, cap: float = 30.0,
                  retry_after: float | None = None,
                  rng: Callable[[], float] = random.random) -> float:
    """Delay before retry number `attempt` (0-based): base*2^attempt, capped, with jitter."""
    delay = min(cap, base * (2 ** attempt)) * (0.5 + 0.5 * rng())
    if retry_after is not None:
        delay = max(delay, min(retry_after, MAX_RETRY_AFTER))
    return delay


async def stream_with_retry(
    open_stream: Callable[[], AsyncIterator[StreamEvent]],
    *,
    max_retries: int,
    base: float = 1.0,
    cap: float = 30.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: Callable[[], float] = random.random,
) -> AsyncIterator[StreamEvent]:
    """Yield events from `open_stream()`, retrying retryable failures.

    A retry only happens if nothing has been yielded yet: once the user has seen part of
    an answer, restarting would duplicate text, so the error is raised instead.
    A RetryEvent is yielded before each wait so the UI can tell the user what is going on.
    """
    attempt = 0
    while True:
        started = False
        try:
            async with aclosing(open_stream()) as inner:
                async for event in inner:
                    started = True
                    yield event
            return
        except OmniRouteError as exc:
            if started or not exc.retryable or attempt >= max_retries:
                raise
            error = exc  # `exc` is unbound after the except block
        delay = backoff_delay(attempt, base=base, cap=cap,
                              retry_after=error.retry_after, rng=rng)
        attempt += 1
        yield RetryEvent(attempt=attempt, max_retries=max_retries, delay=delay, error=error)
        await sleep(delay)
