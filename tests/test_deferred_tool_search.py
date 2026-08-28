from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from core.runtime_state import RuntimeState
from services.compaction.token_estimator import estimate_serialized_tokens
from services.context.message_store import MessageStore
from services.guard import SandboxBoundary, SandboxGuard
from services.permissions import PermissionPolicy, SessionPermissionStore
from services.subagents.runner import SubagentRunner
from services.tools.executor import RegistryToolExecutor
from services.tools.registry import (
    LOADED_TOOL_NAMES_METADATA_KEY,
    ToolExposureConfig,
    ToolRegistry,
)
from services.tools.types import (
    ToolCall,
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolRuntime,
)
from tools.tool_search import descriptor as tool_search_descriptor
from ui.cli.runtime_tool_composition import RuntimeToolComposition
from ui.cli.types import CliRuntime


CORE_NAMES = ("read_file", "grep", "glob", "edit_file", "write_file", "bash")


def _descriptor(
    name: str,
    description: str = "General tool capability.",
    *,
    search_hint: str = "",
    called: list[str] | None = None,
) -> ToolDescriptor:
    def handle(_tool_input: dict[str, object], runtime: ToolRuntime) -> ToolExecutionResult:
        if called is not None:
            called.append(name)
        return ToolExecutionResult(runtime.tool_call_id, name, f"{name} completed")

    return ToolDescriptor(
        name=name,
        description=description,
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=handle,
        search_hint=search_hint,
        classify_input=lambda _input, _runtime: ToolCallClassification(
            read_only=True,
            modifies_filesystem=False,
            concurrency_safe=True,
        ),
    )


def _deferred_descriptors(*, long_tail_count: int = 25) -> tuple[ToolDescriptor, ...]:
    return (
        *(_descriptor(name, f"Core coding primitive {name}.") for name in CORE_NAMES),
        _descriptor("agent", "Delegate work to an isolated subagent."),
        _descriptor(
            "mcp_github_pull_request",
            "Create, inspect, and update a GitHub pull request.",
            search_hint="github pull request review",
        ),
        *(
            _descriptor(
                f"special_tool_{index}",
                f"Specialized long-tail capability {index}.",
            )
            for index in range(long_tail_count - 1)
        ),
        tool_search_descriptor(),
    )


def _names(registry: ToolRegistry, state: RuntimeState) -> set[str]:
    return {item["function"]["name"] for item in registry.tool_schemas(state)}


def _execute(
    executor: RegistryToolExecutor,
    tool_call: ToolCall,
    state: RuntimeState,
) -> ToolExecutionResult:
    async def collect() -> ToolExecutionResult:
        updates = [update async for update in executor.execute((tool_call,), state)]
        return next(update.result for update in updates if update.result is not None)

    return asyncio.run(collect())


def test_small_toolset_exposes_every_normal_schema_without_tool_search() -> None:
    descriptors = (*(_descriptor(name) for name in CORE_NAMES), *(_descriptor(f"tool_{index}") for index in range(24)), tool_search_descriptor())
    registry = ToolRegistry(descriptors)
    state = RuntimeState()

    assert registry.is_deferred_mode(state) is False
    assert _names(registry, state) == {descriptor.name for descriptor in descriptors if descriptor.name != "tool_search"}
    assert "tool_search" not in _names(registry, state)


def test_count_threshold_enters_deferred_mode_with_core_agent_and_search_only() -> None:
    registry = ToolRegistry(_deferred_descriptors())
    state = RuntimeState()

    names = _names(registry, state)

    assert registry.is_deferred_mode(state) is True
    assert set(CORE_NAMES).issubset(names)
    assert {"agent", "tool_search"}.issubset(names)
    assert "mcp_github_pull_request" not in names
    assert "special_tool_0" not in names


def test_schema_token_threshold_enters_deferred_mode_below_count_threshold() -> None:
    descriptors = (
        _descriptor("read_file"),
        _descriptor("mcp_heavy", "External capability " + "schema " * 500),
        tool_search_descriptor(),
    )
    registry = ToolRegistry(
        descriptors,
        exposure_config=ToolExposureConfig(max_direct_schema_tokens=100),
    )
    state = RuntimeState()

    assert registry.is_deferred_mode(state) is True
    assert _names(registry, state) == {"read_file", "tool_search"}


def test_tool_search_loads_matching_schema_then_normal_executor_runs_it() -> None:
    called: list[str] = []
    descriptors = list(_deferred_descriptors())
    descriptors = [
        _descriptor(
            "mcp_github_pull_request",
            "Create, inspect, and update a GitHub pull request.",
            search_hint="github pull request review",
            called=called,
        )
        if descriptor.name == "mcp_github_pull_request"
        else descriptor
        for descriptor in descriptors
    ]
    registry = ToolRegistry(tuple(descriptors))
    state = RuntimeState(session_id="tool-search-flow")
    executor = RegistryToolExecutor(registry)

    initial = _names(registry, state)
    search_result = _execute(
        executor,
        ToolCall(id="search-1", name="tool_search", input={"query": "github pull request"}),
        state,
    )
    payload = json.loads(search_result.content)
    following = _names(registry, state)
    tool_result = _execute(
        executor,
        ToolCall(id="github-1", name="mcp_github_pull_request", input={}),
        state,
    )

    assert search_result.is_error is False
    assert [item["name"] for item in payload["tools"]] == ["mcp_github_pull_request"]
    assert "mcp_github_pull_request" not in initial
    assert "mcp_github_pull_request" in following
    assert "special_tool_0" not in following
    assert state.metadata[LOADED_TOOL_NAMES_METADATA_KEY] == ("mcp_github_pull_request",)
    assert "mcp_github_pull_request" not in {
        descriptor.name for descriptor in registry.deferred_descriptors(state)
    }
    assert tool_result.is_error is False
    assert called == ["mcp_github_pull_request"]


def test_tool_search_no_match_never_exposes_the_deferred_catalog() -> None:
    registry = ToolRegistry(_deferred_descriptors())
    state = RuntimeState()
    executor = RegistryToolExecutor(registry)

    result = _execute(
        executor,
        ToolCall(id="search-empty", name="tool_search", input={"query": "quantum telescope"}),
        state,
    )
    payload = json.loads(result.content)

    assert payload["tools"] == []
    assert LOADED_TOOL_NAMES_METADATA_KEY not in state.metadata
    assert "mcp_github_pull_request" not in _names(registry, state)
    assert "special_tool_0" not in _names(registry, state)


def test_tool_search_schema_budget_includes_previously_loaded_tools() -> None:
    first_alpha = _descriptor(
        "first_alpha",
        "First search capability for an isolated deferred workflow "
        + "details " * 250,
    )
    first_beta = _descriptor(
        "first_beta",
        "First search capability for a second isolated deferred workflow "
        + "details " * 250,
    )
    second_small = _descriptor(
        "second_a_small",
        "Second search capability with a small schema.",
    )
    second_large = _descriptor(
        "second_z_large",
        "Second search capability with an oversized schema " + "payload " * 100,
    )
    descriptors = (
        *(_descriptor(name) for name in CORE_NAMES),
        _descriptor("agent", "Delegate work to an isolated subagent."),
        first_alpha,
        first_beta,
        second_small,
        second_large,
        tool_search_descriptor(),
    )
    state = RuntimeState()
    provisional = ToolRegistry(
        descriptors,
        exposure_config=ToolExposureConfig(
            max_direct_tool_count=1,
            max_loaded_schema_tokens=1_000_000,
        ),
    )
    initial_visible_schema_tokens = provisional._schema_tokens(
        provisional.visible_descriptors(state)
    )
    max_loaded_schema_tokens = (
        initial_visible_schema_tokens
        + provisional._schema_tokens((first_alpha,))
        + provisional._schema_tokens((first_beta,))
        + provisional._schema_tokens((second_small,))
    )
    registry = ToolRegistry(
        descriptors,
        exposure_config=ToolExposureConfig(
            max_direct_tool_count=1,
            max_loaded_schema_tokens=max_loaded_schema_tokens,
        ),
    )

    first_selection = registry.search_and_load(state, "first")
    second_selection = registry.search_and_load(state, "second")
    final_visible_schema_tokens = estimate_serialized_tokens(
        registry.tool_schemas(state)
    )

    assert {descriptor.name for descriptor in first_selection.loaded} == {
        "first_alpha",
        "first_beta",
    }
    assert first_selection.schema_budget_reached is False
    assert [descriptor.name for descriptor in second_selection.loaded] == [
        "second_a_small"
    ]
    assert second_selection.schema_budget_reached is True
    assert {
        "first_alpha",
        "first_beta",
        "second_a_small",
    }.issubset(_names(registry, state))
    assert "second_z_large" not in _names(registry, state)
    assert final_visible_schema_tokens <= max_loaded_schema_tokens


def test_hidden_disabled_and_permission_denied_tools_cannot_be_discovered() -> None:
    state = RuntimeState()
    state.metadata["hidden_tools"] = {"hidden_github"}
    state.metadata["disabled_tools"] = {"disabled_github"}
    store = SessionPermissionStore()
    store.deny_tool("denied_github")
    policy = PermissionPolicy(store)
    descriptors = (
        *(_descriptor(name) for name in CORE_NAMES),
        _descriptor("hidden_github", "GitHub pull request administration."),
        _descriptor("disabled_github", "GitHub pull request administration."),
        _descriptor("denied_github", "GitHub pull request administration."),
        *(_descriptor(f"tail_{index}") for index in range(28)),
        tool_search_descriptor(),
    )
    registry = ToolRegistry(descriptors, permission_policy=policy)

    selection = registry.search_and_load(state, "github pull request")

    assert selection.candidates == ()
    assert {"hidden_github", "disabled_github", "denied_github"}.isdisjoint(_names(registry, state))


class _FakeModelClient:
    def __init__(self) -> None:
        self.config = SimpleNamespace(display_name="Replacement", model="replacement-model")

    async def stream(self, snapshot: object):
        raise AssertionError("model calls are not expected in this rebuild test")
        yield


class _UnusedExecutor:
    async def execute(self, tool_calls: tuple, state: object):
        if False:
            yield


def test_provider_rebuild_preserves_deferred_config_loaded_tools_and_disabled_agent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state = RuntimeState(session_id="rebuild-tool-search")
    state.metadata[LOADED_TOOL_NAMES_METADATA_KEY] = ("mcp_github_pull_request",)
    message_store = MessageStore(
        transcript_root=tmp_path / ".nervure",
        session_id=state.session_id,
        cwd=tmp_path,
        flush_interval_seconds=60,
    )
    config = ToolExposureConfig(max_direct_tool_count=1)
    base = tuple(
        descriptor
        for descriptor in _deferred_descriptors(long_tail_count=2)
        if descriptor.name != "agent"
    )
    policy = PermissionPolicy(SessionPermissionStore())
    guard = SandboxGuard(SandboxBoundary(cwd=tmp_path))
    runtime = CliRuntime(
        workspace=tmp_path,
        state=state,
        message_store=message_store,
        registry=ToolRegistry(base, permission_policy=policy, exposure_config=config),
        model_client=_FakeModelClient(),
        tool_executor=_UnusedExecutor(),  # type: ignore[arg-type]
        permission_policy=policy,
        guard=guard,
        base_descriptors=base,
        tool_composition=RuntimeToolComposition(
            base_descriptors=base,
            exposure_config=config,
            agent_enabled=False,
        ),
    )
    monkeypatch.setattr("ui.cli.types.create_model_client", lambda _path: _FakeModelClient())

    rebound = runtime.with_model_config()

    assert rebound.registry is not None
    assert rebound.registry.exposure_config == config
    assert "mcp_github_pull_request" in _names(rebound.registry, state)
    assert "agent" not in _names(rebound.registry, state)
