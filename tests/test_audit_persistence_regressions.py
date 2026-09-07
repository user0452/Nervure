from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, RLock
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from evals import headless
from services.context.transcript import JsonlTranscriptStore
from services.tasks.store import TaskStore, TaskStoreError
from services.observability.sinks import JsonlTraceSink
from services.observability.error_log import JsonlErrorLogSink


def test_flush_open_failure_preserves_pending_messages(tmp_path, monkeypatch):
    store = JsonlTranscriptStore(tmp_path, "old", flush_interval_seconds=3600)
    store.append_message({"role": "user", "content": "keep me"},
                         message_uuid="one", parent_uuid=None)
    real_open = Path.open

    def fail_open(path, *args, **kwargs):
        if path == store.messages_path:
            raise OSError("synthetic disk failure")
        return real_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", fail_open)
        with pytest.raises(OSError, match="synthetic"):
            store.flush()

    store.flush()
    assert [item.uuid for item in store.load_messages()] == ["one"]


@pytest.mark.parametrize("store_class,filename", [
    (JsonlTranscriptStore, "messages.jsonl"),
    (JsonlTraceSink, "trace.jsonl"),
    (JsonlErrorLogSink, "errors.jsonl"),
])
def test_flush_serializes_session_switch_and_disk_write(
    tmp_path, monkeypatch, store_class, filename,
):
    store = store_class(tmp_path, "old", flush_interval_seconds=3600)
    store._enqueue_line('{"session_id":"old"}')
    disk_entered, switch_attempted, release_disk = Event(), Event(), Event()
    real_mkdir = Path.mkdir
    actual_lock = RLock()

    class ObservedLock:
        def __enter__(self):
            if disk_entered.is_set():
                switch_attempted.set()
            actual_lock.acquire()
            return self

        def __exit__(self, *args):
            actual_lock.release()

    store._lock = ObservedLock()

    def paused_mkdir(path, *args, **kwargs):
        if path == tmp_path / "old":
            disk_entered.set()
            assert release_disk.wait(5)
        return real_mkdir(path, *args, **kwargs)

    with monkeypatch.context() as patch, ThreadPoolExecutor(2) as pool:
        patch.setattr(Path, "mkdir", paused_mkdir)
        writer = pool.submit(store.flush)
        try:
            assert disk_entered.wait(5)
            switcher = pool.submit(store.switch_session, "new")
            assert switch_attempted.wait(5)
            # With serialization, the writer owns the lock until the disk
            # operation finishes. An unlocked implementation can switch here.
            lock_is_free = actual_lock.acquire(blocking=False)
            if lock_is_free:
                actual_lock.release()
                switcher.result(timeout=5)
            assert store.session_id == "old"
        finally:
            release_disk.set()
            writer.result(timeout=5)
        switcher.result(timeout=5)

    assert (tmp_path / "old" / filename).is_file()
    assert not (tmp_path / "new" / filename).exists()
    store.flush()


def test_headless_shutdown_runs_runtime_close_before_export(tmp_path, monkeypatch):
    calls = []

    async def close():
        calls.append("close")

    def export(*args):
        assert calls == ["close"]
        calls.append("export")

    sink = SimpleNamespace(flush=lambda: None, trace_path=None)
    runtime = SimpleNamespace(
        close=AsyncMock(side_effect=close),
        message_store=SimpleNamespace(flush_transcript=lambda: None),
        trace_recorder=sink,
        error_log_recorder=sink,
        mcp_manager=None,
    )
    real_path = Path
    monkeypatch.setattr(headless, "Path", lambda value: (
        tmp_path / "logs" if str(value) == "/logs/agent/nervure" else real_path(value)
    ))
    monkeypatch.setattr(headless, "_write_git_diff", export)
    asyncio.run(headless._shutdown(runtime, tmp_path))
    runtime.close.assert_awaited_once()
    assert calls == ["close", "export"]


@pytest.mark.parametrize("task_id", ["../victim", "..\\victim", "/victim", "C:\\victim", "a:b"])
def test_task_ids_cannot_escape_the_task_list(tmp_path, task_id):
    store = TaskStore(tmp_path)
    with pytest.raises(TaskStoreError):
        store.task_path("session", task_id)


@pytest.mark.parametrize("list_id", ["..", "../victim", "..\\victim", "/victim"])
def test_task_list_ids_cannot_escape_the_store(tmp_path, list_id):
    with pytest.raises(TaskStoreError):
        TaskStore(tmp_path).tasks_dir(list_id)


def test_task_delete_rejects_traversal_without_removing_file(tmp_path):
    store = TaskStore(tmp_path)
    directory = store.tasks_dir("session")
    directory.mkdir(parents=True)
    victim = directory.parent / "victim.json"
    victim.write_text('{"valuable": true}', encoding="utf-8")
    with pytest.raises(TaskStoreError):
        store.delete_task("session", "../victim")
    assert victim.is_file()


@pytest.mark.parametrize("events", [
    [],
    [SimpleNamespace(type="suspended", text="", metadata={})],
    [SimpleNamespace(type="completed", text="partial", metadata={"status": "partial"})],
])
def test_headless_incomplete_run_is_not_success(events):
    async def stream(*args, **kwargs):
        for event in events:
            yield event

    runtime = SimpleNamespace(loop=SimpleNamespace(stream=stream))
    with pytest.raises(RuntimeError, match="did not complete"):
        asyncio.run(headless._consume_stream(runtime, "fixture", ()))


def test_headless_completed_run_returns_final_text():
    async def stream(*args, **kwargs):
        yield SimpleNamespace(type="completed", text="done", metadata={"status": "completed"})

    runtime = SimpleNamespace(loop=SimpleNamespace(stream=stream))
    assert asyncio.run(headless._consume_stream(runtime, "fixture", ())) == "done"
