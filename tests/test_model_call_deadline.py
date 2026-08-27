from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from infrastructure.config.env import ResolvedProviderConfig, load_provider_config
from infrastructure.providers.catalog import get_provider_definition
from infrastructure.providers.chat_completions import OpenAICompatibleChatCompletionsClient
from services.context.snapshot import ContextSnapshot
from services.model.deadline import stream_with_wall_clock_deadline
from services.model.retry import ModelRetryRunner, RetryPolicy
from services.model.stream import ModelStreamEvent
from services.model.types import ProviderError
from services.observability import JsonlTraceSink, TraceRecorder
from infrastructure.filesystem.nervure_paths import sessions_dir


def _config(*, deadline: float = 0.05) -> ResolvedProviderConfig:
    provider = get_provider_definition("openai")
    return ResolvedProviderConfig(
        provider,
        provider.id,
        provider.display_name,
        "https://api.openai.com/v1",
        "gpt-test",
        "secret",
        model_call_timeout_seconds=deadline,
        models_path=provider.models_path,
        chat_completions_path=provider.chat_completions_path,
    )


async def _collect_deadline(
    source: AsyncIterator[ModelStreamEvent],
    *,
    timeout_seconds: float,
) -> list[ModelStreamEvent]:
    return [
        event
        async for event in stream_with_wall_clock_deadline(
            source,
            timeout_seconds=timeout_seconds,
            provider_id="fake",
        )
    ]


def test_endless_valid_events_hit_whole_call_deadline_and_close_source() -> None:
    closed = False

    async def endless() -> AsyncIterator[ModelStreamEvent]:
        nonlocal closed
        try:
            while True:
                yield ModelStreamEvent.content_delta("x")
                await asyncio.sleep(0.01)
        finally:
            closed = True

    async def run() -> None:
        with pytest.raises(ProviderError) as exc_info:
            await _collect_deadline(endless(), timeout_seconds=0.035)
        error = exc_info.value
        assert error.error_type == "timeout_error"
        assert error.retryable is False
        assert error.metadata["timeout_kind"] == "model_call_wall_clock"
        assert error.metadata["wall_clock_deadline_seconds"] == 0.035
        assert error.metadata["partial_text_chars"] > 0
        assert error.metadata["partial_output_visible"] is True

    asyncio.run(run())
    assert closed is True


def test_normal_completion_is_not_mistaken_for_deadline() -> None:
    async def source() -> AsyncIterator[ModelStreamEvent]:
        yield ModelStreamEvent.content_delta("done")
        yield ModelStreamEvent.message_completed(
            assistant_message={"role": "assistant", "content": "done"},
            final_text="done",
            stop_reason="stop",
        )

    async def run() -> list[ModelStreamEvent]:
        return await _collect_deadline(source(), timeout_seconds=0.2)

    events = asyncio.run(run())
    assert events[-1].type == "message_completed"
    assert events[-1].final_text == "done"


def test_timeout_metadata_covers_partial_text_and_tool_stream() -> None:
    async def source() -> AsyncIterator[ModelStreamEvent]:
        yield ModelStreamEvent.content_delta("partial")
        yield ModelStreamEvent.tool_call_delta(metadata={"index": 0, "name": "read_file"})
        await asyncio.sleep(0.2)

    async def run() -> ProviderError:
        with pytest.raises(ProviderError) as exc_info:
            await _collect_deadline(source(), timeout_seconds=0.02)
        return exc_info.value

    error = asyncio.run(run())
    assert error.metadata["partial_text_chars"] == len("partial")
    assert error.metadata["partial_tool_call_count"] == 1


def test_timeout_is_not_retried() -> None:
    calls = 0

    async def source() -> AsyncIterator[ModelStreamEvent]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.1)
        yield ModelStreamEvent.message_completed(
            assistant_message={"role": "assistant", "content": "late"},
            final_text="late",
        )

    runner = ModelRetryRunner(
        policy=RetryPolicy(max_retries=3),
        sleep=lambda _seconds: asyncio.sleep(0),
    )

    async def run() -> None:
        with pytest.raises(ProviderError):
            async for _ in runner.stream(
                lambda: stream_with_wall_clock_deadline(
                    source(), timeout_seconds=0.01, provider_id="fake"
                )
            ):
                pass

    asyncio.run(run())
    assert calls == 1


@dataclass
class _EndlessTransport:
    closed: bool = False

    async def post_json(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("not used")

    async def stream_json_lines(self, *args: Any, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        try:
            while True:
                yield {"choices": [{"delta": {"content": "x"}}]}
                await asyncio.sleep(0.01)
        finally:
            self.closed = True


def test_openai_compatible_adapter_applies_model_call_deadline() -> None:
    transport = _EndlessTransport()
    client = OpenAICompatibleChatCompletionsClient(
        _config(deadline=0.025),
        async_transport=transport,
    )

    async def run() -> None:
        with pytest.raises(ProviderError) as exc_info:
            async for _ in client.stream(ContextSnapshot("", ())):
                pass
        assert exc_info.value.error_type == "timeout_error"
        assert exc_info.value.provider_id == "openai"

    asyncio.run(run())
    assert transport.closed is True


def test_timeout_metadata_is_included_in_span_end_trace(tmp_path: Path) -> None:
    sink = JsonlTraceSink(sessions_dir(tmp_path), "timeout-trace")
    recorder = TraceRecorder(session_id="timeout-trace", sink=sink)
    error = ProviderError(
        "deadline",
        provider_id="fake",
        error_type="timeout_error",
        metadata={
            "timeout_kind": "model_call_wall_clock",
            "elapsed_ms": 12.5,
            "partial_text_chars": 3,
        },
    )
    with pytest.raises(ProviderError):
        with recorder.span("model_call"):
            raise error
    recorder.flush()
    records = [
        line
        for line in sink.trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    end = __import__("json").loads(records[-1])
    assert end["attributes"]["timeout_kind"] == "model_call_wall_clock"
    assert end["attributes"]["partial_text_chars"] == 3


def test_model_call_deadline_is_configurable_and_positive(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "ONECODE_PROVIDER_ID=openai",
                "OPENAI_MODEL=gpt-test",
                "OPENAI_API_KEY=secret",
                "ONECODE_TIMEOUT_SECONDS=7",
                "ONECODE_MODEL_CALL_TIMEOUT_SECONDS=222",
            ]
        ),
        encoding="utf-8",
    )
    config = load_provider_config(env)
    assert config.timeout_seconds == 7
    assert config.model_call_timeout_seconds == 222

    env.write_text(
        "\n".join(
            [
                "ONECODE_PROVIDER_ID=openai",
                "OPENAI_MODEL=gpt-test",
                "OPENAI_API_KEY=secret",
                "ONECODE_MODEL_CALL_TIMEOUT_SECONDS=0",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ProviderError, match="greater than zero"):
        load_provider_config(env)
