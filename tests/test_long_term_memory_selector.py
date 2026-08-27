from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

from core.runtime_state import RuntimeState
from services.memory.auto_store import LongTermMemoryStore
from services.memory.context_preparer import RelevantMemoryContextPreparer
from services.memory.selector import RelevantMemorySelector
from services.memory.types import LongTermMemoryFile
from services.model.stream import ModelStreamEvent
from services.model.types import ModelUsage, ProviderError
from services.observability import JsonlTraceSink, TraceRecorder


class FakeModel:
    def __init__(
        self,
        text: str,
        *,
        usage: ModelUsage | None = None,
        stop_reason: str | None = None,
        reasoning_text: str = "",
    ) -> None:
        self.text = text
        self.usage = usage
        self.stop_reason = stop_reason
        self.reasoning_text = reasoning_text
        self.calls = 0
        self.snapshots = []
        self.config = SimpleNamespace(provider_id="fake", model="selector-test")

    async def stream(self, snapshot) -> AsyncIterator[ModelStreamEvent]:
        self.calls += 1
        self.snapshots.append(snapshot)
        yield ModelStreamEvent.message_completed(
            assistant_message={"role": "assistant", "content": self.text},
            final_text=self.text,
            stop_reason=self.stop_reason,
            reasoning_text=self.reasoning_text,
            usage=self.usage,
        )


class FailingModel(FakeModel):
    async def stream(self, snapshot) -> AsyncIterator[ModelStreamEvent]:
        self.calls += 1
        if False:
            yield ModelStreamEvent.message_completed(
                assistant_message={"role": "assistant", "content": ""},
                final_text="",
            )
        raise ProviderError(
            "selector unavailable",
            provider_id="fake",
            error_type="network_error",
            retryable=True,
        )


def _store_with_topic(tmp_path: Path) -> LongTermMemoryStore:
    store = LongTermMemoryStore(tmp_path)
    store.ensure_exists()
    (store.memory_dir / "a.md").write_text(
        "---\nname: A\ndescription: project A\ntype: project\n---\nA details",
        encoding="utf-8",
    )
    return store


def _recorder(tmp_path: Path) -> TraceRecorder:
    return TraceRecorder(
        session_id="selector-test",
        workspace=tmp_path,
        sink=JsonlTraceSink(tmp_path / ".onecode", "selector-test", flush_interval_seconds=60),
    )


def test_selector_accepts_json_and_filters_unknown_items():
    catalog = tuple(
        LongTermMemoryFile(
            path=Path(f"{name}.md"),
            relative_path=f"{name}.md",
            name=name,
            description=name,
            type="project",
            mtime=1.0,
        )
        for name in ("a", "b", "c", "d", "e", "f")
    )
    selector = RelevantMemorySelector(
        FakeModel(
            '{"selected_memories":["a.md","missing.md","b.md","c.md","d.md","e.md","f.md"]}'
        )
    )

    selected = asyncio.run(
        selector.select(
            ({"role": "user", "content": "hello"},),
            RuntimeState(),
            catalog,
        )
    )

    assert [item.relative_path for item in selected] == [
        "a.md",
        "b.md",
        "c.md",
        "d.md",
        "e.md",
    ]


def test_selector_requests_strict_structured_output_schema():
    memory = LongTermMemoryFile(
        path=Path("a.md"),
        relative_path="a.md",
        name="a",
        description="a",
        type="project",
        mtime=1.0,
    )
    model = FakeModel('{"selected_memories": []}')

    asyncio.run(
        RelevantMemorySelector(model).select(
            (),
            RuntimeState(),
            (memory,),
        )
    )

    structured = model.snapshots[0].usage_hints["structured_output"]
    assert structured["name"] == "long_term_memory_selection"
    assert structured["strict"] is True
    schema = structured["schema"]
    assert schema["required"] == ["selected_memories"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["selected_memories"]["maxItems"] == 5


def test_selector_returns_empty_on_invalid_json():
    memory = LongTermMemoryFile(
        path=Path("a.md"),
        relative_path="a.md",
        name="a",
        description="a",
        type="project",
        mtime=1.0,
    )

    selected = asyncio.run(
        RelevantMemorySelector(FakeModel("not json")).select(
            (),
            RuntimeState(),
            (memory,),
        )
    )

    assert selected == ()


def test_selector_uses_isolated_small_output_budget_without_mutating_main_overrides() -> None:
    memory = LongTermMemoryFile(
        path=Path("a.md"),
        relative_path="a.md",
        name="a",
        description="a",
        type="project",
        mtime=1.0,
    )
    model = FakeModel('{"selected_memories": []}')
    state = RuntimeState()
    state.metadata["model_request_overrides"] = {"max_output_tokens": 64000}

    asyncio.run(RelevantMemorySelector(model).select((), state, (memory,)))

    assert model.snapshots[0].usage_hints["request_overrides"] == {
        "max_output_tokens": 256,
    }
    assert state.metadata["model_request_overrides"] == {"max_output_tokens": 64000}
    assert "reasoning_effort" not in model.snapshots[0].usage_hints["request_overrides"]


def test_empty_catalog_does_not_call_selector_model(tmp_path: Path) -> None:
    model = FakeModel('{"selected_memories": []}')
    selector = RelevantMemorySelector(model)

    selected = asyncio.run(selector.select(({"role": "user", "content": "hello"},), RuntimeState(), ()))

    assert selected == ()
    assert model.calls == 0


def test_context_preparer_selects_once_per_user_turn_and_reuses_selection(
    tmp_path: Path,
) -> None:
    store = _store_with_topic(tmp_path)
    model = FakeModel('{"selected_memories": ["a.md"]}')
    state = RuntimeState(session_id="session-a")
    state.begin_user_turn()
    preparer = RelevantMemoryContextPreparer(store, RelevantMemorySelector(model))
    messages = ({"role": "user", "content": "remember this"},)

    first = asyncio.run(preparer.prepare(messages, state))
    second = asyncio.run(
        preparer.prepare(
            (*messages, {"role": "tool_result", "content": "tool output"}),
            state,
        )
    )

    assert model.calls == 1
    assert first.messages[-1]["attachment"]["path"] == "a.md"
    assert second.messages[-1]["attachment"]["path"] == "a.md"

    state.begin_user_turn()
    asyncio.run(preparer.prepare(messages, state))
    assert model.calls == 2


def test_context_preparer_trace_shows_cache_hit_and_selected_paths(
    tmp_path: Path,
) -> None:
    store = _store_with_topic(tmp_path)
    recorder = _recorder(tmp_path)
    model = FakeModel('{"selected_memories": ["a.md"]}')
    state = RuntimeState(session_id="selector-test")
    state.begin_user_turn()
    preparer = RelevantMemoryContextPreparer(
        store,
        RelevantMemorySelector(model, trace_recorder=recorder),
        trace_recorder=recorder,
    )
    messages = ({"role": "user", "content": "hello"},)

    asyncio.run(preparer.prepare(messages, state))
    asyncio.run(preparer.prepare(messages, state))
    recorder.flush()
    records = [
        json.loads(line)
        for line in recorder.trace_path.read_text(encoding="utf-8").splitlines()
    ]
    cache_hit = next(
        record
        for record in records
        if record["name"] == "long_term_memory_selector_cache_hit"
    )

    assert cache_hit["attributes"]["cache_hit"] is True
    assert cache_hit["attributes"]["selected_paths"] == ["a.md"]


def test_catalog_change_invalidates_selection_cache(tmp_path: Path) -> None:
    store = _store_with_topic(tmp_path)
    model = FakeModel('{"selected_memories": ["a.md"]}')
    state = RuntimeState(session_id="session-catalog")
    state.begin_user_turn()
    preparer = RelevantMemoryContextPreparer(store, RelevantMemorySelector(model))
    messages = ({"role": "user", "content": "inspect memory"},)

    asyncio.run(preparer.prepare(messages, state))
    (store.memory_dir / "b.md").write_text(
        "---\nname: B\ndescription: project B\ntype: project\n---\nB details",
        encoding="utf-8",
    )
    asyncio.run(preparer.prepare(messages, state))

    assert model.calls == 2


def test_selector_failure_is_cached_as_empty_and_does_not_block_context(
    tmp_path: Path,
) -> None:
    store = _store_with_topic(tmp_path)
    model = FailingModel("")
    state = RuntimeState(session_id="session-failure")
    state.begin_user_turn()
    preparer = RelevantMemoryContextPreparer(store, RelevantMemorySelector(model))
    messages = ({"role": "user", "content": "continue"},)

    first = asyncio.run(preparer.prepare(messages, state))
    second = asyncio.run(preparer.prepare(messages, state))

    assert model.calls == 1
    assert first.messages == messages
    assert second.messages == messages


def test_selector_trace_contains_paths_and_usage(tmp_path: Path) -> None:
    store = _store_with_topic(tmp_path)
    recorder = _recorder(tmp_path)
    model = FakeModel(
        '{"selected_memories": ["a.md"]}',
        usage=ModelUsage(input_tokens=10, output_tokens=2, cache_read_input_tokens=3),
    )
    state = RuntimeState(session_id="selector-test")
    state.begin_user_turn()

    asyncio.run(
        RelevantMemorySelector(model, trace_recorder=recorder).select(
            ({"role": "user", "content": "hello"},),
            state,
            store.scan(),
        )
    )
    recorder.flush()
    records = [
        json.loads(line)
        for line in recorder.trace_path.read_text(encoding="utf-8").splitlines()
    ]
    completed = next(
        record
        for record in records
        if record["name"] == "long_term_memory_selector_completed"
    )

    assert completed["attributes"]["selected_paths"] == ["a.md"]
    assert completed["attributes"]["input_tokens"] == 10
    assert completed["attributes"]["cache_read_input_tokens"] == 3
    assert completed["attributes"]["uncached_input_tokens"] == 7
    assert completed["attributes"]["output_tokens"] == 2
    assert completed["attributes"]["reasoning_tokens_status"] == "adapter_unavailable"
    assert completed["attributes"]["parse_success"] is True
    assert completed["attributes"]["final_text_chars"] == len(
        '{"selected_memories": ["a.md"]}'
    )
    assert "final_text" not in completed["attributes"]


def test_selector_trace_records_provider_reasoning_details_when_available(tmp_path: Path) -> None:
    store = _store_with_topic(tmp_path)
    recorder = _recorder(tmp_path)
    model = FakeModel(
        '{"selected_memories": ["a.md"]}',
        usage=ModelUsage(
            input_tokens=10,
            output_tokens=8,
            cache_read_input_tokens=3,
            reasoning_tokens=5,
            visible_output_tokens=3,
        ),
    )
    state = RuntimeState(session_id="selector-usage-details")
    state.begin_user_turn()

    asyncio.run(
        RelevantMemorySelector(model, trace_recorder=recorder).select(
            ({"role": "user", "content": "hello"},),
            state,
            store.scan(),
        )
    )
    recorder.flush()
    completed = next(
        json.loads(line)
        for line in recorder.trace_path.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["name"] == "long_term_memory_selector_completed"
    )

    assert completed["attributes"]["reasoning_tokens"] == 5
    assert completed["attributes"]["reasoning_tokens_status"] == "available"
    assert completed["attributes"]["visible_output_tokens"] == 3


def test_selector_emits_parse_failed_trace_for_truncated_json(tmp_path: Path) -> None:
    store = _store_with_topic(tmp_path)
    recorder = _recorder(tmp_path)
    text = '{"selected_memories":["a.md"'
    model = FakeModel(
        text,
        stop_reason="length",
        usage=ModelUsage(input_tokens=10, output_tokens=256),
    )
    state = RuntimeState(session_id="selector-parse-failure")
    state.begin_user_turn()

    selected = asyncio.run(
        RelevantMemorySelector(model, trace_recorder=recorder).select(
            ({"role": "user", "content": "hello"},),
            state,
            store.scan(),
        )
    )
    recorder.flush()
    records = [
        json.loads(line)
        for line in recorder.trace_path.read_text(encoding="utf-8").splitlines()
    ]
    parse_failed = next(
        record
        for record in records
        if record["name"] == "long_term_memory_selector_parse_failed"
    )
    completed = next(
        record
        for record in records
        if record["name"] == "long_term_memory_selector_completed"
    )

    assert selected == ()
    assert parse_failed["attributes"]["final_text_chars"] == len(text)
    assert parse_failed["attributes"]["stop_reason"] == "length"
    assert parse_failed["attributes"]["output_tokens"] == 256
    assert parse_failed["attributes"]["parse_success"] is False
    assert completed["attributes"]["parse_success"] is False


def test_selector_debug_trace_records_input_and_visible_output_without_memory_body(
    tmp_path: Path,
) -> None:
    store = _store_with_topic(tmp_path)
    recorder = TraceRecorder(
        session_id="selector-debug",
        workspace=tmp_path,
        sink=JsonlTraceSink(tmp_path / ".onecode", "selector-debug", flush_interval_seconds=60),
        trace_level="debug",
    )
    model = FakeModel(
        '{"selected_memories": ["a.md"]}',
        reasoning_text="I matched the project topic.",
        usage=ModelUsage(input_tokens=12, output_tokens=6, reasoning_tokens=2),
    )
    state = RuntimeState(session_id="selector-debug")
    state.begin_user_turn()

    asyncio.run(
        RelevantMemorySelector(model, trace_recorder=recorder).select(
            ({"role": "user", "content": "inspect project"},),
            state,
            store.scan(),
        )
    )
    recorder.flush()
    records = [
        json.loads(line)
        for line in recorder.trace_path.read_text(encoding="utf-8").splitlines()
    ]
    started = next(record for record in records if record["name"] == "long_term_memory_selector_started")
    completed = next(record for record in records if record["name"] == "long_term_memory_selector_completed")

    assert started["attributes"]["selector_input_metadata"]["catalog_paths"] == ["a.md"]
    assert completed["attributes"]["assistant_visible_text"] == '{"selected_memories": ["a.md"]}'
    assert completed["attributes"]["provider_reasoning_text"] == "I matched the project topic."
    assert completed["attributes"]["selected_paths"] == ["a.md"]
    assert "A details" not in json.dumps(records, ensure_ascii=False)
