"""Regression coverage for deferred memory across real runtime lifecycles."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.runtime_state import RuntimeState
from services.context.message_store import MessageStore
from services.hooks import HookEvent
from services.memory.auto_store import LongTermMemoryStore
from services.memory.extraction import LongTermMemoryExtractionService
from services.mcp.types import McpConfigSet, McpConnectionSnapshot
from services.subagents.types import SubagentResult
from ui.cli import app


class RecordingRunner:
    def __init__(self):
        self.requests = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def run(self, request):
        self.requests.append(request)
        self.started.set()
        await self.release.wait()
        return SubagentResult(agent_type="fork", session_id="child", final_text="ok")


class NoNetworkModel:
    config = SimpleNamespace(provider_id="test", display_name="Test", model="test")

    async def stream(self, snapshot):
        raise AssertionError("Lifecycle tests must never call a real provider")
        yield


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "create_model_client", lambda *a, **k: NoNetworkModel())
    monkeypatch.setattr(app, "load_project_mcp_config", lambda *a: McpConfigSet())
    monkeypatch.setattr(
        app.McpConnectionManager, "connect_all_blocking",
        lambda *a: McpConnectionSnapshot(),
    )
    monkeypatch.setattr(app, "build_mcp_tool_descriptors", lambda *a: ())
    runtime = app.build_runtime(tmp_path)
    runtime.mcp_manager = None
    yield runtime
    runtime.long_term_memory_extractor._cancel_idle_timer()
    runtime.message_store.flush_transcript()


def make_service(tmp_path, message_store, runner):
    return LongTermMemoryExtractionService(
        LongTermMemoryStore(tmp_path),
        message_store=message_store,
        subagent_runner=runner,
    )


@pytest.mark.parametrize("flush_immediately", [False, True])
def test_pre_compact_hook_retains_snapshot_and_ids(runtime, monkeypatch, flush_immediately):
    runner = RecordingRunner()
    runtime.long_term_memory_extractor.bind_subagent_runner(runner)
    runtime.message_store.append_user("PRE_COMPACT_ONLY_FACT")
    for index in range(24):
        runtime.message_store.append_user(f"later user {index} " + "x" * 2000)
        runtime.message_store.append_assistant({"content": "y" * 2000})
    original_ids = runtime.message_store.current_message_ids()

    async def immediate_summary(*args, **kwargs):
        return "Summary without the original fact", None

    monkeypatch.setattr(runtime.compaction_service, "_request_compact_summary", immediate_summary)

    async def body():
        await runtime.compaction_service.manual_compact(runtime.state)
        if flush_immediately:
            await runtime.close()
        else:
            await asyncio.sleep(0)
        assert "PRE_COMPACT_ONLY_FACT" in runner.requests[0].prompt
        if not flush_immediately:
            metadata = runtime.state.metadata["long_term_memory_extraction"]
            assert metadata["last_ltm_consolidated_message_id"] == original_ids[-1]

    asyncio.run(body())


def test_session_rebind_uses_new_store_and_watermark_path(runtime, tmp_path):
    runtime.message_store.append_user("OLD_SESSION_FACT")
    old_dir = runtime.message_store.transcript_store.session_dir
    runner = RecordingRunner()
    runtime.long_term_memory_extractor.bind_subagent_runner(runner)
    state = RuntimeState()
    store = MessageStore(transcript_root=tmp_path / "new-sessions", session_id=state.session_id)
    store.append_user("NEW_SESSION_FACT")
    rebuilt = runtime.with_session(state=state, message_store=store)

    asyncio.run(rebuilt.long_term_memory_extractor.consolidate(trigger="idle", state=state))

    assert "NEW_SESSION_FACT" in runner.requests[0].prompt
    assert "OLD_SESSION_FACT" not in runner.requests[0].prompt
    assert (store.transcript_store.session_dir / "ltm_watermark.json").is_file()
    assert not (old_dir / "ltm_watermark.json").exists()


@pytest.mark.parametrize("entry", ["command", "selector"])
def test_resume_flushes_old_session_before_rebinding(runtime, monkeypatch, entry):
    from ui.cli.commands import dispatch_command_async
    from ui.cli.terminal import repl as repl_module

    target_store = MessageStore(
        transcript_root=runtime.message_store.transcript_store.root_dir,
        session_id="resume-target",
        cwd=runtime.workspace,
    )
    target_store.append_user("RESTORED_SESSION_FACT")
    target_store.flush_transcript()
    runtime.message_store.append_user("OLD_SESSION_PENDING_FACT")
    old_dir = runtime.message_store.transcript_store.session_dir
    runner = RecordingRunner()
    runtime.long_term_memory_extractor.bind_subagent_runner(runner)
    summary = SimpleNamespace(session_id="resume-target", title="Saved session")

    class Selector:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self):
            return SimpleNamespace(value=summary)

    monkeypatch.setattr(repl_module, "list_session_summaries", lambda workspace: (summary,))
    monkeypatch.setattr(repl_module, "TransientSelector", Selector)

    async def body():
        if entry == "command":
            result = await dispatch_command_async(runtime, "/continue resume-target")
        else:
            repl = repl_module.InlineRepl(runtime)
            result = await repl._run_resume_selector()
        assert result.runtime is not None
        assert result.runtime.state.session_id == "resume-target"
        assert len(runner.requests) == 1
        assert "OLD_SESSION_PENDING_FACT" in runner.requests[0].prompt
        assert (old_dir / "ltm_watermark.json").is_file()

        await result.runtime.close()
        assert len(runner.requests) == 2
        assert "RESTORED_SESSION_FACT" in runner.requests[-1].prompt
        assert "OLD_SESSION_PENDING_FACT" not in runner.requests[-1].prompt

    asyncio.run(body())


def test_watermark_survives_actual_transcript_restore(tmp_path):
    state = RuntimeState()
    store = MessageStore(transcript_root=tmp_path, session_id=state.session_id)
    store.append_user("ALREADY_CONSOLIDATED")
    store.append_assistant({"content": "acknowledged"})
    asyncio.run(make_service(tmp_path, store, RecordingRunner()).consolidate(trigger="idle", state=state))
    store.flush_transcript()

    restored_state = RuntimeState()
    restored = MessageStore.from_transcript(store.transcript_store, restored_state)
    runner = RecordingRunner()
    asyncio.run(make_service(tmp_path, restored, runner).consolidate(trigger="idle", state=restored_state))

    assert restored.current_message_ids() == store.current_message_ids()
    assert runner.requests == []


def test_clear_keeps_new_message_ids_aligned(tmp_path):
    state = RuntimeState()
    store = MessageStore(transcript_root=tmp_path, session_id=state.session_id)
    store.append_user("old question")
    store.append_assistant({"content": "old answer"})
    old_ids = set(store.current_message_ids())
    store.clear_for_new_session(state.start_new_session())
    runner = RecordingRunner()
    service = make_service(tmp_path, store, runner)
    store.append_user("FIRST_NEW_TURN")
    asyncio.run(service.consolidate(trigger="idle", state=state))
    store.append_user("SECOND_NEW_TURN")
    asyncio.run(service.consolidate(trigger="idle", state=state))

    assert len(store.current_message_ids()) == len(store.current_messages()) == 2
    assert old_ids.isdisjoint(store.current_message_ids())
    assert len(runner.requests) == 2
    assert "SECOND_NEW_TURN" in runner.requests[-1].prompt


@pytest.mark.parametrize("command", ["/clear", "/exit"])
def test_session_commands_do_not_deadlock_active_consolidation(tmp_path, command):
    # A subprocess timeout also catches regressions that block the event loop
    # itself, which asyncio.wait_for cannot interrupt.
    code = r"""
import asyncio
import sys
from pathlib import Path
from core.runtime_state import RuntimeState
from services.context.message_store import MessageStore
from services.hooks import HookRegistry, HookEvent
from services.memory.auto_store import LongTermMemoryStore
from services.memory.extraction import LongTermMemoryExtractionService
from services.subagents.types import SubagentResult
from ui.cli.commands import dispatch_command_async
from ui.cli.types import CliRuntime

async def body():
    state = RuntimeState()
    old_session = state.session_id
    started = asyncio.Event()
    class Runner:
        async def run(self, request):
            started.set()
            await asyncio.sleep(0.05)
            return SubagentResult(agent_type="fork", session_id="child", final_text="ok")
    store = MessageStore(transcript_root=Path(sys.argv[1]), session_id=state.session_id)
    store.append_user("pending")
    watermark = store.transcript_store.session_dir / "ltm_watermark.json"
    service = LongTermMemoryExtractionService(
        LongTermMemoryStore(Path(sys.argv[1])), message_store=store, subagent_runner=Runner()
    )
    hooks = HookRegistry()
    async def flush(payload):
        await service.flush(trigger="session_switch", state=state)
    hooks.register(HookEvent.SESSION_CLOSE, flush)
    hooks.register(HookEvent.SESSION_SWITCH, flush)
    runtime = CliRuntime(
        workspace=Path(sys.argv[1]), state=state, message_store=store, hooks=hooks,
        long_term_memory_extractor=service,
    )
    task = asyncio.create_task(service.consolidate(trigger="idle", state=state))
    await started.wait()
    result = await dispatch_command_async(runtime, sys.argv[2])
    await task
    assert watermark.exists()
    if sys.argv[2] == "/exit":
        assert result.should_exit
    else:
        assert result.runtime.state.session_id != old_session
    print("completed")
asyncio.run(body())
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), command],
        capture_output=True, text=True, timeout=10,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert result.returncode == 0, result.stderr
    assert "completed" in result.stdout


@pytest.mark.parametrize("shutdown_path", ["tty", "batch"])
def test_normal_shutdown_flushes_memory_once(runtime, shutdown_path):
    runner = RecordingRunner()
    runtime.long_term_memory_extractor.bind_subagent_runner(runner)
    runtime.message_store.append_user("FACT_PENDING_AT_EXIT")
    hook_calls = []
    runtime.hooks.register(HookEvent.SESSION_CLOSE, lambda payload: hook_calls.append(True))

    async def body():
        runtime.long_term_memory_extractor.mark_dirty(runtime.state)
        if shutdown_path == "tty":
            from ui.cli.terminal.repl import InlineRepl
            repl = InlineRepl.__new__(InlineRepl)
            repl._runtime = runtime
            await repl._shutdown_async()
        else:
            from ui.cli.batch import _shutdown
            await _shutdown(runtime)
        await runtime.close()
        assert len(runner.requests) == 1
        assert "FACT_PENDING_AT_EXIT" in runner.requests[0].prompt
        assert hook_calls == [True]
        assert runtime.long_term_memory_extractor._idle_timer is None

    asyncio.run(body())


def test_pending_pre_compact_snapshot_survives_later_trigger(tmp_path):
    async def body():
        state = RuntimeState()
        store = MessageStore(transcript_root=tmp_path, session_id=state.session_id)
        store.append_user("initial")
        runner = RecordingRunner()
        runner.release.clear()
        service = make_service(tmp_path, store, runner)
        task = asyncio.create_task(service.consolidate(trigger="idle", state=state))
        await runner.started.wait()
        store.append_user("FACT_DISCARDED_BY_COMPACTION")
        await service.consolidate(trigger="full_compact", state=state)
        store.replace_messages_for_compaction(
            ({"role": "user", "content": "summary", "metadata": {"is_compact_summary": True}},),
            reason="manual",
        )
        store.append_user("AFTER_COMPACT_FACT")
        await service.consolidate(trigger="idle", state=state)
        runner.release.set()
        await task
        assert len(runner.requests) == 2
        assert "FACT_DISCARDED_BY_COMPACTION" in runner.requests[-1].prompt
        assert "AFTER_COMPACT_FACT" in runner.requests[-1].prompt

    asyncio.run(body())


@pytest.mark.parametrize("watermark", ["[]", "null", '"invalid"', "{broken"])
def test_invalid_watermark_file_does_not_disable_extraction(tmp_path, watermark):
    state = RuntimeState()
    store = MessageStore(transcript_root=tmp_path, session_id=state.session_id)
    store.append_user("pending")
    session_dir = store.transcript_store.session_dir
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "ltm_watermark.json").write_text(watermark, encoding="utf-8")
    runner = RecordingRunner()
    asyncio.run(make_service(tmp_path, store, runner).consolidate(trigger="idle", state=state))
    assert len(runner.requests) == 1
    saved = json.loads((session_dir / "ltm_watermark.json").read_text(encoding="utf-8"))
    assert saved["last_ltm_consolidated_message_id"] == store.current_message_ids()[-1]


def test_new_prompt_cancels_idle_timer_until_turn_stops(runtime):
    async def body():
        extractor = runtime.long_term_memory_extractor
        extractor._policy = replace(extractor._policy, idle_debounce_seconds=0.01)
        runner = RecordingRunner()
        extractor.bind_subagent_runner(runner)
        runtime.message_store.append_user("first turn")
        extractor.mark_dirty(runtime.state)
        timer = extractor._idle_timer

        await runtime.hooks.run(HookEvent.USER_PROMPT_SUBMIT, {"state": runtime.state})
        assert timer.cancelled()
        await asyncio.sleep(0.03)
        assert runner.requests == []

        runtime.message_store.append_user("next turn")
        extractor.mark_dirty(runtime.state)
        await asyncio.wait_for(runner.started.wait(), timeout=1)
        await runtime.close()
        assert len(runner.requests) == 1

    asyncio.run(body())


def test_flush_after_cancelled_worker_includes_messages_newer_than_pending(tmp_path):
    async def body():
        state = RuntimeState()
        store = MessageStore(transcript_root=tmp_path, session_id=state.session_id)
        store.append_user("initial")
        runner = RecordingRunner()
        runner.release.clear()
        service = make_service(tmp_path, store, runner)
        task = asyncio.create_task(service.consolidate(trigger="idle", state=state))
        await runner.started.wait()
        store.append_user("pending snapshot")
        await service.consolidate(trigger="idle", state=state)
        store.append_user("LATEST_FACT_BEFORE_EXIT")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        runner.release.set()
        await service.flush(trigger="session_close", state=state)
        assert "LATEST_FACT_BEFORE_EXIT" in runner.requests[-1].prompt
        assert state.metadata["long_term_memory_extraction"][
            "last_ltm_consolidated_message_id"
        ] == store.current_message_ids()[-1]

    asyncio.run(body())
