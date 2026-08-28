from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from core.runtime_state import RuntimeState
from infrastructure.filesystem.nervure_paths import sessions_dir
from services.context.message_store import MessageStore
from services.mcp.types import McpConfigSet, McpConnectionSnapshot
from services.tools.types import ToolCall, ToolDescriptor, ToolExecutionResult
from ui.cli import app
from ui.cli import types as cli_types


class FakeModelClient:
    config = SimpleNamespace(
        provider_id="test",
        display_name="TestProvider",
        model="test-model",
    )

    async def stream(self, snapshot):
        raise AssertionError("production composition smoke test must not call provider")
        yield


def _mcp_probe_descriptor() -> ToolDescriptor:
    def handler(_tool_input, runtime):
        return ToolExecutionResult(runtime.tool_call_id, "mcp_probe", "ok")

    return ToolDescriptor(
        name="mcp_probe",
        description="Read external documentation from an MCP server.",
        input_schema={"type": "object", "properties": {}},
        handler=handler,
        search_hint="MCP external docs",
    )


def _patch_external_runtime_boundaries(monkeypatch) -> None:
    monkeypatch.setattr(app, "create_model_client", lambda *_args, **_kwargs: FakeModelClient())
    monkeypatch.setattr(
        cli_types,
        "create_model_client",
        lambda *_args, **_kwargs: FakeModelClient(),
    )
    monkeypatch.setattr(app, "load_project_mcp_config", lambda _workspace: McpConfigSet())
    monkeypatch.setattr(
        app.McpConnectionManager,
        "connect_all_blocking",
        lambda _manager: McpConnectionSnapshot(),
    )
    monkeypatch.setattr(
        app,
        "build_mcp_tool_descriptors",
        lambda _manager: (_mcp_probe_descriptor(),),
    )


def test_production_runtime_composition_shares_checkpoint_store(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _patch_external_runtime_boundaries(monkeypatch)

    runtime = app.build_runtime(tmp_path)

    assert runtime.tool_executor.checkpoint_store is runtime.checkpoint_store
    assert runtime.subagent_runner.checkpoint_store is runtime.checkpoint_store
    assert runtime.session_state_store is not None
    assert runtime.session_state_store.path.is_file()

    async def write_file() -> None:
        updates = []
        async for update in runtime.tool_executor.execute(
            (
                ToolCall(
                    id="write-smoke",
                    name="write_file",
                    input={"file_path": "smoke.txt", "content": "ok\n"},
                ),
            ),
            runtime.state,
        ):
            updates.append(update)
        assert updates[-1].result is not None
        assert updates[-1].result.is_error is False

    asyncio.run(write_file())
    assert (tmp_path / "smoke.txt").read_text(encoding="utf-8") == "ok\n"
    assert runtime.checkpoint_store.latest(runtime.state.session_id) is not None

    next_state = RuntimeState(session_id="rebuilt-session")
    next_store = MessageStore(
        transcript_root=sessions_dir(tmp_path),
        session_id=next_state.session_id,
        cwd=tmp_path,
        flush_interval_seconds=60,
    )
    rebuilt = runtime.with_session(state=next_state, message_store=next_store)
    all_before = {descriptor.name for descriptor in rebuilt.registry.descriptors()}
    rebuilt.state.metadata["tool_discovery_query"] = "shell command"
    before_names = {
        descriptor.name
        for descriptor in rebuilt.registry.visible_descriptors(rebuilt.state)
    }
    reconfigured = rebuilt.with_model_config()
    assert reconfigured.tool_executor.checkpoint_store is runtime.checkpoint_store
    assert reconfigured.subagent_runner.checkpoint_store is runtime.checkpoint_store
    after_names = {
        descriptor.name
        for descriptor in reconfigured.registry.visible_descriptors(reconfigured.state)
    }
    all_after = {
        descriptor.name for descriptor in reconfigured.registry.descriptors()
    }
    assert all_after == all_before
    assert before_names == after_names
    assert {"read_file", "glob", "grep", "bash"} <= after_names
    assert "mcp_probe" not in after_names

    reconfigured.state.metadata["tool_discovery_query"] = "external docs"
    assert "mcp_probe" in {
        descriptor.name
        for descriptor in reconfigured.registry.visible_descriptors(
            reconfigured.state
        )
    }


def test_model_reconfiguration_preserves_disabled_agent_and_discovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _patch_external_runtime_boundaries(monkeypatch)
    monkeypatch.setenv("NERVURE_DISABLE_AGENT_TOOL", "1")

    runtime = app.build_runtime(tmp_path)
    normal_names = {descriptor.name for descriptor in runtime.registry.descriptors()}
    runtime.state.metadata["tool_discovery_query"] = "shell command"
    visible_before = {
        descriptor.name
        for descriptor in runtime.registry.visible_descriptors(runtime.state)
    }
    reconfigured = runtime.with_model_config()
    visible_after = {
        descriptor.name
        for descriptor in reconfigured.registry.visible_descriptors(
            reconfigured.state
        )
    }

    assert "agent" not in normal_names
    assert {
        descriptor.name for descriptor in reconfigured.registry.descriptors()
    } == normal_names
    assert visible_after == visible_before
    assert visible_after < normal_names
