from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from core.runtime_state import RuntimeState
from infrastructure.filesystem.nervure_paths import sessions_dir
from services.context.message_store import MessageStore
from services.mcp.types import McpConfigSet, McpConnectionSnapshot
from services.tools.types import ToolCall
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


def test_production_runtime_composition_shares_checkpoint_store(
    tmp_path: Path,
    monkeypatch,
) -> None:
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
    reconfigured = rebuilt.with_model_config()
    assert reconfigured.tool_executor.checkpoint_store is runtime.checkpoint_store
    assert reconfigured.subagent_runner.checkpoint_store is runtime.checkpoint_store
