"""Provider-neutral wall-clock deadline for streaming model calls."""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator
from time import perf_counter
from typing import TypeVar

from services.model.stream import ModelStreamEvent
from services.model.types import ProviderError


_EventT = TypeVar("_EventT")


async def stream_with_wall_clock_deadline(
    events: AsyncIterator[_EventT],
    *,
    timeout_seconds: float,
    provider_id: str | None = None,
) -> AsyncIterator[_EventT]:
    """Forward a stream while bounding its complete wall-clock lifetime.

    The HTTP transport still owns connection/read timeouts. This helper
    bounds the whole model call, including providers that keep emitting
    otherwise-valid events forever. Only progress counts are recorded.
    """

    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("model call timeout must be finite and positive")

    started_at = perf_counter()
    text_chars = 0
    reasoning_chars = 0
    tool_call_count = 0
    iterator = events.__aiter__()
    timeout_scope = asyncio.timeout(timeout_seconds)
    try:
        async with timeout_scope:
            while True:
                try:
                    event = await iterator.__anext__()
                except StopAsyncIteration:
                    return
                if isinstance(event, ModelStreamEvent):
                    if event.type == "content_delta":
                        text_chars += len(event.text)
                    elif event.type == "reasoning_delta":
                        reasoning_chars += len(event.text)
                    elif event.type == "message_completed":
                        text_chars = max(text_chars, len(event.final_text))
                        reasoning_chars = max(reasoning_chars, len(event.reasoning_text))
                    elif event.type == "tool_call_completed":
                        tool_call_count += 1
                    elif event.type == "tool_call_delta":
                        index = event.metadata.get("index")
                        if isinstance(index, int):
                            tool_call_count = max(tool_call_count, index + 1)
                yield event
    except asyncio.CancelledError:
        await _close_iterator(iterator)
        raise
    except TimeoutError as exc:
        await _close_iterator(iterator)
        if not timeout_scope.expired():
            # A provider/transport may raise its own TimeoutError before the
            # wall-clock scope expires; preserve that source error.
            raise
        elapsed_ms = round((perf_counter() - started_at) * 1000, 3)
        raise ProviderError(
            "Model call exceeded its wall-clock deadline.",
            provider_id=provider_id,
            error_type="timeout_error",
            retryable=False,
            metadata={
                "timeout_kind": "model_call_wall_clock",
                "wall_clock_deadline_seconds": timeout_seconds,
                "elapsed_ms": elapsed_ms,
                "partial_text_chars": text_chars,
                "partial_reasoning_chars": reasoning_chars,
                "partial_tool_call_count": tool_call_count,
                "partial_output_visible": bool(text_chars or tool_call_count),
            },
        ) from exc
    except BaseException:
        await _close_iterator(iterator)
        raise


async def _close_iterator(iterator: object) -> None:
    close = getattr(iterator, "aclose", None)
    if callable(close):
        await close()
