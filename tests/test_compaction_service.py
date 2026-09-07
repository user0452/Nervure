from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from core.runtime_state import RuntimeState
from core.context_engine import ContextEngine
from services.compaction import ContextCompactionService
from services.context.current_model_context import CurrentModelContext
from services.context.snapshot import ContextSnapshot
from services.model.stream import ModelStreamEvent
from services.model.types import LLMResponse, ModelUsage
from services.model.types import ProviderError
from utils.toolResultStorage import ToolResultStorage
from services.compaction.service import MICROCOMPACT_PLACEHOLDER
from services.compaction.types import CompactionConfig, CompactionTrigger
from services.context.message_store import MessageStore
from services.tools.types import ToolExecutionResult


@dataclass
class FakeModelClient:
    response: LLMResponse
    snapshots: list[ContextSnapshot] = field(default_factory=list)

    async def stream(self, snapshot: ContextSnapshot):
        self.snapshots.append(snapshot)
        yield ModelStreamEvent.message_completed(
            assistant_message=self.response.assistant_message,
            final_text=self.response.final_text,
            tool_calls=self.response.tool_calls,
            usage=self.response.usage,
        )


def _prepare(
    service: ContextCompactionService,
    messages: tuple[dict, ...],
    state: RuntimeState,
):
    return asyncio.run(service.prepare_for_model(messages, state))


def test_final_budget_compacts_raw_transcript_not_synthetic_projection(tmp_path) -> None:
    state = RuntimeState(session_id="session-final-budget")
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        flush_interval_seconds=60,
    )
    message_store.append_user("raw user message")
    model = FakeModelClient(
        LLMResponse(
            assistant_message={"role": "assistant", "content": "summary"},
            final_text="summary",
        )
    )
    service = ContextCompactionService(
        config=CompactionConfig(
            context_window_tokens=100,
            recent_tail_min_tokens=0,
            recent_tail_max_tokens=50,
        ),
        message_store=message_store,
        model_client=model,
    )
    synthetic = {
        "role": "user",
        "content": "attachment " * 100,
        "attachment": {"type": "file"},
    }
    snapshot = ContextSnapshot(
        system_prompt="system",
        messages=(*message_store.current_messages(), synthetic),
        tool_schemas=({"name": "tool", "description": "schema " * 50},),
    )

    compacted = asyncio.run(service.ensure_final_context_budget(snapshot, state))

    assert compacted is True
    compact_request = model.snapshots[0]
    assert compact_request.system_prompt == ""
    assert compact_request.tool_schemas == ()
    assert all(message.get("attachment") is None for message in compact_request.messages)
    assert "attachment " not in str(compact_request.messages)
    stored = message_store.current_messages()
    assert all(message.get("attachment") != {"type": "file"} for message in stored)
    assert any(message.get("metadata", {}).get("is_compact_summary") for message in stored)

    rebuilt = ContextSnapshot(
        system_prompt="system " * 100,
        messages=message_store.current_messages(),
    )
    with pytest.raises(ProviderError, match="remains over budget"):
        service.validate_final_context_budget(rebuilt, state)


def test_prepare_for_model_persists_large_tool_results_before_projection(tmp_path) -> None:
    state = RuntimeState(session_id="session-compact")
    store = ToolResultStorage(tmp_path / ".onecode" / state.session_id)
    service = ContextCompactionService(
        config=CompactionConfig(
            tool_result_budget_chars=3,
            tool_result_preview_chars=2,
            microcompact_keep_recent=1,
        ),
        result_store=store,
    )
    messages = (
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "name": "grep"}],
        },
        {
            "role": "tool_result",
            "tool_call_id": "call-1",
            "tool_name": "grep",
            "content": "abcdef",
        },
    )

    result = _prepare(service, messages, state)

    projected_result = result.messages[1]
    assert result.trigger == CompactionTrigger.MICRO
    assert projected_result["metadata"]["result_stored"] is True
    assert "Preview:\nab" in projected_result["content"]
    assert result.transcript_refs == (projected_result["metadata"]["stored_result_path"],)
    assert (store.results_dir / "call-1.txt").read_text(encoding="utf-8") == "abcdef"
    assert messages[1]["content"] == "abcdef"
    assert state.metadata["last_compaction"]["trigger"] == "micro"


def test_prepare_for_model_reuses_stored_large_tool_result_reference(tmp_path) -> None:
    state = RuntimeState(session_id="session-compact")
    store = ToolResultStorage(tmp_path / ".onecode" / state.session_id)
    service = ContextCompactionService(
        config=CompactionConfig(
            tool_result_budget_chars=3,
            tool_result_preview_chars=2,
            microcompact_keep_recent=1,
        ),
        result_store=store,
    )
    messages = (
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "name": "grep"}],
        },
        {
            "role": "tool_result",
            "tool_call_id": "call-1",
            "tool_name": "grep",
            "content": "abcdef",
        },
    )

    first = _prepare(service, messages, state)
    second = _prepare(service, messages, state)

    first_metadata = first.messages[1]["metadata"]
    second_metadata = second.messages[1]["metadata"]
    assert second_metadata["stored_result_id"] == first_metadata["stored_result_id"]
    assert second_metadata["stored_result_path"] == first_metadata["stored_result_path"]
    assert second.transcript_refs == first.transcript_refs
    assert sorted(path.name for path in store.results_dir.iterdir()) == ["call-1.txt"]


def test_prepare_for_model_microcompacts_old_tool_results_only() -> None:
    state = RuntimeState()
    service = ContextCompactionService(
        config=CompactionConfig(
            tool_result_budget_chars=100,
            microcompact_keep_recent=1,
        )
    )
    messages = (
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "old", "name": "read_file"}],
        },
        {
            "role": "tool_result",
            "tool_call_id": "old",
            "tool_name": "read_file",
            "content": "old content",
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "recent", "name": "read_file"}],
        },
        {
            "role": "tool_result",
            "tool_call_id": "recent",
            "tool_name": "read_file",
            "content": "recent content",
        },
    )

    result = _prepare(service, messages, state)

    assert result.messages[1]["content"] == MICROCOMPACT_PLACEHOLDER
    assert result.messages[1]["metadata"]["microcompacted"] is True
    assert result.messages[3]["content"] == "recent content"


def test_prepare_method_can_be_used_as_context_preparer() -> None:
    state = RuntimeState()
    service = ContextCompactionService(
        config=CompactionConfig(
            tool_result_budget_chars=100,
            microcompact_keep_recent=0,
        )
    )
    messages = (
        {"role": "tool_result", "tool_call_id": "call-1", "content": "content"},
    )

    prepared = asyncio.run(service.prepare(messages, state))

    assert prepared.messages[0]["content"] == MICROCOMPACT_PLACEHOLDER


def test_compaction_preparer_populates_context_snapshot_refs_and_hints(tmp_path) -> None:
    state = RuntimeState(session_id="session-compact")
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        flush_interval_seconds=60,
    )
    message_store.append_assistant(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "name": "grep"}],
        }
    )
    message_store.append_tool_results(
        [
            ToolExecutionResult(
                tool_call_id="call-1",
                tool_name="grep",
                content="abcdef",
            )
        ]
    )
    store = ToolResultStorage(message_store.transcript_store.session_dir)
    service = ContextCompactionService(
        config=CompactionConfig(
            tool_result_budget_chars=3,
            tool_result_preview_chars=2,
            microcompact_keep_recent=1,
        ),
        result_store=store,
    )
    engine = ContextEngine(message_store, context_preparer=service)

    snapshot = asyncio.run(engine.build_for_model(state))

    assert snapshot.usage_hints["compaction_trigger"] == "micro"
    assert snapshot.usage_hints["token_after"] > 0
    assert snapshot.transcript_refs == (
        snapshot.messages[1]["metadata"]["stored_result_path"],
    )


def test_full_compact_uses_one_cache_safe_snapshot_request(tmp_path) -> None:
    state = RuntimeState(session_id="session-full-compact")
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        flush_interval_seconds=60,
    )
    parent_messages = (
        {"role": "user", "content": "Implement the feature"},
        {"role": "assistant", "content": "Current implementation"},
    )
    for message in parent_messages:
        if message["role"] == "user":
            message_store.append_user(message["content"])
        else:
            message_store.append_assistant(message)
    parent_tools = ({"name": "read_file", "input_schema": {"type": "object"}},)
    current_context = CurrentModelContext(
        ContextSnapshot(
            system_prompt="PARENT SYSTEM",
            messages=parent_messages,
            tool_schemas=parent_tools,
        )
    )
    model = FakeModelClient(
        LLMResponse(
            assistant_message={"role": "assistant", "content": "summary"},
            final_text=(
                "<summary># Task\nImplement the feature\n# Next Step\nContinue.</summary>"
            ),
            usage=ModelUsage(input_tokens=120, output_tokens=12, cache_read_input_tokens=100),
        )
    )
    service = ContextCompactionService(
        message_store=message_store,
        model_client=model,
        current_model_context=current_context,
        config=CompactionConfig(
            context_window_tokens=128_000,
            recent_tail_min_tokens=1,
            recent_tail_max_tokens=10_000,
        ),
    )

    result = asyncio.run(service.manual_compact(state, focus="feature"))

    assert len(model.snapshots) == 1
    request = model.snapshots[0]
    assert request.system_prompt == current_context.snapshot.system_prompt
    assert request.tool_schemas == current_context.snapshot.tool_schemas
    assert request.messages[: len(parent_messages)] == parent_messages
    assert request.messages[-1]["metadata"]["is_compact_instruction"] is True
    assert "system prompt" in request.messages[-1]["content"]
    assert "tool definitions/schemas" in request.messages[-1]["content"]
    assert result.metadata["summary_output_tokens"] == 12
    assert message_store.current_messages()[0]["metadata"]["is_compact_boundary"] is True
    message_store.flush_transcript()
    assert message_store.transcript_store.messages_path.exists()


def test_full_compact_without_snapshot_uses_safe_projected_fallback(tmp_path) -> None:
    state = RuntimeState(session_id="session-full-fallback")
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        flush_interval_seconds=60,
    )
    message_store.append_user("fallback task")
    model = FakeModelClient(
        LLMResponse(
            assistant_message={"role": "assistant", "content": "summary"},
            final_text="<summary>safe fallback</summary>",
        )
    )
    service = ContextCompactionService(
        message_store=message_store,
        model_client=model,
        current_model_context=CurrentModelContext(),
    )

    result = asyncio.run(service.manual_compact(state))

    assert result.metadata["source"] == "full"
    assert len(model.snapshots) == 1
    assert model.snapshots[0].system_prompt == ""
    assert model.snapshots[0].tool_schemas == ()
    assert model.snapshots[0].messages[-1]["metadata"]["is_compact_instruction"] is True


def test_full_compact_never_executes_model_tool_calls(tmp_path) -> None:
    state = RuntimeState(session_id="session-full-tool-error")
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        flush_interval_seconds=60,
    )
    message_store.append_user("task")
    model = FakeModelClient(
        LLMResponse(
            assistant_message={"role": "assistant", "content": []},
            final_text="",
            tool_calls=(),
        )
    )
    # The provider-neutral event can still report a tool call via metadata in
    # a custom client; this fake is replaced below with that one-event client.
    class ToolCallModel(FakeModelClient):
        async def stream(self, snapshot: ContextSnapshot):
            self.snapshots.append(snapshot)
            yield ModelStreamEvent.message_completed(
                assistant_message={"role": "assistant", "content": []},
                final_text="",
                tool_calls=(object(),),  # type: ignore[arg-type]
            )

    model = ToolCallModel(model.response)
    service = ContextCompactionService(message_store=message_store, model_client=model)

    try:
        asyncio.run(service.manual_compact(state))
    except RuntimeError as exc:
        assert "tool calls" in str(exc)
    else:
        raise AssertionError("compact tool calls must fail without a second request")
    assert len(model.snapshots) == 1


def test_recent_tail_keeps_latest_user_turn_and_tool_pair() -> None:
    service = ContextCompactionService(
        config=CompactionConfig(
            context_window_tokens=128_000,
            compact_recent_tail_ratio=0.01,
            recent_tail_min_tokens=1,
            recent_tail_max_tokens=1,
        )
    )
    messages = (
        {"role": "user", "content": "old task"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "latest task"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "name": "read_file"}],
        },
        {
            "role": "tool_result",
            "tool_call_id": "call-1",
            "tool_name": "read_file",
            "content": "latest result",
        },
    )

    tail = service._recent_tail(messages)

    assert tail == messages[2:]


def test_recent_tail_does_not_retain_entire_oversized_user_turn():
    service = ContextCompactionService(config=CompactionConfig(
        recent_tail_min_tokens=0, recent_tail_max_tokens=256,
    ))
    messages = (
        {"role": "user", "content": "Preserve the public API."},
        *({"role": "assistant", "content": "already summarized " * 100} for _ in range(30)),
        {"role": "assistant", "content": "", "tool_calls": [{"id": "last", "name": "read_file"}]},
        {"role": "tool_result", "tool_call_id": "last", "content": "latest result"},
    )
    tail = service._recent_tail(messages)
    assert tail[0] == messages[0]
    assert tail[-2:] == messages[-2:]
    assert len(tail) == 3
