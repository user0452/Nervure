"""Opt-in, bounded real-provider checks using synthetic data only.

Run with --allow-live --config .env --output outputs/<new-run-directory>.
This is a module smoke suite, not a coding benchmark or an OS sandbox.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from time import perf_counter
from unittest.mock import patch

from core.runtime_state import RuntimeState
from infrastructure.config.env import load_provider_config
from infrastructure.providers.chat_completions import OpenAICompatibleChatCompletionsClient
from services.compaction import ContextCompactionService
from services.context.current_model_context import CurrentModelContext
from services.context.message_store import MessageStore
from services.context.snapshot import ContextSnapshot
from services.guard import SandboxBoundary, SandboxGuard
from services.memory import LongTermMemoryStore, RelevantMemorySelector
from services.memory.extraction import LongTermMemoryExtractionService
from services.model.types import ModelUsage
from services.observability import TraceRecorder
from services.permissions import PermissionPolicy, PermissionResponse, SessionPermissionStore
from services.subagents.runner import SubagentRunner
from services.subagents.types import SubagentRequest
from services.tools.executor import RegistryToolExecutor
from services.tools.registry import ToolRegistry
from tools.read_file import descriptor as read_descriptor
from tools.edit_file import descriptor as edit_descriptor
from tools.write_file import descriptor as write_descriptor
from ui.cli import app
from utils.toolResultStorage import ToolResultStorage

CASE_NAMES = (
    "stream", "tool_roundtrip", "memory_selector", "full_compaction",
    "explore_subagent", "memory_extraction", "runtime_file_edit_and_close",
)


class LiveBudgetExceeded(RuntimeError):
    pass


class MeteredClient:
    """Reserve before I/O; retain reservations even after errors/cancellation.

    UTF-8 payload bytes plus 8192 framing tokens is a conservative estimate,
    not a tokenizer guarantee. Actual provider usage is reported separately.
    Missing usage never becomes zero-cost permission for another request.
    """

    def __init__(self, inner, *, budget=1_000_000, max_calls=30, max_output=4096):
        if not 0 < budget <= 1_000_000 or not 0 < max_calls <= 30 or not 0 < max_output <= 4096:
            raise ValueError("Live limits exceed the authorized smoke-test bounds.")
        self.inner = inner
        self.config = inner.config
        self.budget = budget
        self.max_calls = max_calls
        self.max_output = max_output
        self.reserved = 0
        self.calls = []
        self.label = "setup"
        self.on_update = lambda: None

    async def stream(self, snapshot):
        hints = dict(snapshot.usage_hints)
        overrides = dict(hints.get("request_overrides") or {})
        requested = overrides.get("max_output_tokens", self.max_output)
        limit = min(requested, self.max_output) if type(requested) is int and requested > 0 else self.max_output
        hints["request_overrides"] = {**overrides, "max_output_tokens": limit}
        bounded = replace(snapshot, usage_hints=hints)
        payload = self.inner._build_payload(bounded)
        reservation = len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + limit + 8192
        if len(self.calls) >= self.max_calls or self.reserved + reservation > self.budget:
            raise LiveBudgetExceeded("Live request budget exhausted before sending.")
        self.reserved += reservation
        record = {
            "case": self.label, "reservation": reservation,
            "max_output_tokens": limit, "usage": None, "status": "started",
        }
        self.calls.append(record)
        self.on_update()
        usage = None
        started = perf_counter()
        try:
            async for event in self.inner.stream(bounded):
                if event.usage is not None:
                    usage = event.usage
                if event.type == "message_completed":
                    record["stop_reason"] = event.stop_reason
                    record["output_interrupted"] = event.output_interrupted
                    record["final_text_chars"] = len(event.final_text)
                yield event
            record["status"] = "completed"
        except BaseException as exc:
            record["status"] = "failed"
            record["error_type"] = type(exc).__name__
            record["provider_error_type"] = getattr(exc, "error_type", None)
            record["status_code"] = getattr(exc, "status_code", None)
            raise
        finally:
            if usage is not None:
                record["usage"] = asdict(usage)
                actual = usage.input_tokens + usage.output_tokens
                self.reserved += max(0, actual - reservation)
            record["duration_ms"] = round((perf_counter() - started) * 1000, 2)
            self.on_update()
            print(json.dumps({
                "call": len(self.calls), "case": record["case"],
                "status": record["status"], "usage": record["usage"],
            }), flush=True)

    def summary(self):
        total = ModelUsage()
        missing = 0
        for call in self.calls:
            if call["usage"] is None:
                missing += 1
            else:
                total.add(ModelUsage(**call["usage"]))
        return {
            "budget": self.budget, "reserved_tokens": self.reserved,
            "actual_usage": asdict(total),
            "actual_total_tokens": total.input_tokens + total.output_tokens,
            "calls_without_usage": missing, "calls": self.calls,
        }


async def completion(client, snapshot):
    completed = None
    async for event in client.stream(snapshot):
        if event.type == "message_completed":
            completed = event
    assert completed is not None, "no completed message"
    assert not completed.output_interrupted, "output interrupted"
    return completed


def make_store(workspace, session_id):
    return MessageStore(
        transcript_root=workspace / ".nervure" / "sessions",
        session_id=session_id, cwd=workspace, flush_interval_seconds=3600,
    )


class FixturePermissionPrompter:
    async def request_permission(self, request):
        # The fixture runtime exposes only file/search tools. No shell,
        # external directory, network, skill or delegated tool is approved.
        targets = request.classification.targets
        safe = bool(targets) and all(
            target.kind == "file"
            and (self.workspace / (target.normalized_value or target.value)).resolve().is_relative_to(self.workspace)
            for target in targets
        )
        return PermissionResponse(action="allow" if safe else "deny", scope="once")

    def __init__(self, workspace):
        self.workspace = workspace.resolve()


async def run_suite(client, output, *, cases=CASE_NAMES):
    results = []
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "provider": client.config.provider_id, "model": client.config.model,
        "scope": "synthetic module smoke, not Harbor or SWE-bench",
        "requested_cases": list(cases),
        "cases": results,
    }

    def save():
        (output / "summary.json").write_text(
            json.dumps({**report, **client.summary()}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    client.on_update = save

    async def case(name, operation):
        client.label = name
        start = perf_counter()
        try:
            evidence = await asyncio.wait_for(operation(), timeout=300)
            results.append({"name": name, "status": "pass", "evidence": evidence})
        except Exception as exc:
            results.append({"name": name, "status": "fail", "error_type": type(exc).__name__})
            if isinstance(exc, AssertionError):
                results[-1]["assertion"] = str(exc)
        results[-1]["duration_ms"] = round((perf_counter() - start) * 1000, 2)
        save()
        print(json.dumps(results[-1]), flush=True)

    with tempfile.TemporaryDirectory(prefix="nervure-live-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        workspace.mkdir()
        home = root / "home"
        home.mkdir()
        fixture = workspace / "fixture.py"
        fixture.write_text('def audit_marker():\n    return "NERVURE_FIXTURE_73219"\n', encoding="utf-8")
        descriptors = (read_descriptor(), edit_descriptor(), write_descriptor())
        state = RuntimeState(session_id="live-modules")
        state.metadata["workspace"] = str(workspace)
        store = make_store(workspace, state.session_id)
        guard = SandboxGuard(SandboxBoundary(cwd=workspace))
        policy = PermissionPolicy(SessionPermissionStore())
        runner = SubagentRunner(
            workspace=workspace, transcript_root=workspace / ".nervure" / "sessions",
            parent_message_store=store, current_model_context=CurrentModelContext(),
            model_client=client, base_descriptors=descriptors, guard=guard,
            permission_policy=policy, permission_prompter=FixturePermissionPrompter(workspace),
            trace_recorder=TraceRecorder.noop(),
        )

        async def stream_check():
            event = await completion(client, ContextSnapshot(
                "Follow the requested output format.",
                ({"role": "user", "content": "Reply with exactly NERVURE_OK and nothing else."},),
            ))
            assert event.final_text.strip() == "NERVURE_OK", f"Unexpected synthetic echo: {event.final_text!r}"
            return {"exact_output": True}

        async def tool_check():
            registry = ToolRegistry([read_descriptor()])
            executor = RegistryToolExecutor(registry, guard=guard)
            snapshot = ContextSnapshot(
                "Read fixture.py using read_file, then report its marker verbatim.",
                ({"role": "user", "content": "What marker is returned by audit_marker?"},),
                registry.tool_schemas(state),
            )
            event = await completion(client, snapshot)
            calls = event.metadata.get("tool_calls", ())
            assert len(calls) == 1 and calls[0].name == "read_file"
            blocks = [
                update.result async for update in executor.execute(calls, state)
                if update.result is not None
            ]
            assert len(blocks) == 1 and not blocks[0].is_error
            followup = replace(snapshot, messages=snapshot.messages + (
                event.assistant_message,
                {"role": "tool_result", "tool_call_id": calls[0].id, "content": blocks[0].content},
            ))
            final = await completion(client, followup)
            assert "NERVURE_FIXTURE_73219" in final.final_text
            return {"tool_calls": 1, "result_roundtrip": True}

        memory = LongTermMemoryStore(workspace)
        memory.ensure_exists()
        (memory.memory_dir / "testing.md").write_text(
            "---\nname: Testing\ndescription: Python pytest test commands\ntype: project\n---\nUse uv run pytest tests -q.\n",
            encoding="utf-8",
        )
        (memory.memory_dir / "colors.md").write_text(
            "---\nname: Colors\ndescription: Website color palette\ntype: project\n---\nThe theme is green.\n",
            encoding="utf-8",
        )

        async def selector_check():
            selected = await RelevantMemorySelector(client).select(
                ({"role": "user", "content": "How should I run the Python tests? Select the test command memory only."},),
                state, memory.scan(),
            )
            paths = [item.relative_path for item in selected]
            assert paths == ["testing.md"]
            return {"selected_paths": paths}

        async def compact_check():
            compact_store = make_store(workspace, "compact")
            compact_state = RuntimeState(session_id="compact")
            compact_store.append_user("Keep AUDIT_TARGET_73219 and the constraint: never change the public API.")
            for index in range(48):
                compact_store.append_assistant({"content": f"Inspection {index}: " + "Synthetic code findings. " * 100})
            service = ContextCompactionService(
                message_store=compact_store,
                result_store=ToolResultStorage(compact_store.transcript_store.session_dir),
                model_client=client,
            )
            try:
                result = await service.manual_compact(
                    compact_state, focus="Preserve AUDIT_TARGET_73219 and the public API constraint.",
                )
                text = json.dumps(result.messages)
                assert "AUDIT_TARGET_73219" in text and "API" in text, "summary lost the target or API constraint"
                assert result.token_after < result.token_before, f"context grew: {result.token_before} -> {result.token_after}"
                return {"estimated_before": result.token_before, "estimated_after": result.token_after}
            finally:
                compact_store.flush_transcript()

        async def explore_check():
            before = hashlib.sha256(fixture.read_bytes()).hexdigest()
            result = await runner.run(SubagentRequest(
                prompt="Read fixture.py. Return the exact marker from audit_marker and its file path.",
                subagent_type="Explore", parent_session_id=state.session_id,
                parent_tool_call_id="explore-smoke", metadata={"max_turns": 4},
            ))
            assert not result.is_error and result.tool_result_count > 0
            assert "NERVURE_FIXTURE_73219" in result.final_text
            assert hashlib.sha256(fixture.read_bytes()).hexdigest() == before
            return {"tool_results": result.tool_result_count, "fixture_unchanged": True}

        async def extraction_check():
            store.append_user(
                "Remember this durable project rule for future sessions: "
                "the audit artifact prefix is AUDIT_PREFIX_73219. Always use it for audit filenames."
            )
            store.append_assistant({"content": "I will retain this durable project rule."})
            extractor = LongTermMemoryExtractionService(memory, subagent_runner=runner, message_store=store)
            await extractor.consolidate(trigger="explicit", state=state)
            assert state.metadata["long_term_memory_extraction"]["last_status"] == "success"
            texts = "\n".join(p.read_text(encoding="utf-8") for p in memory.memory_dir.glob("*.md"))
            assert "AUDIT_PREFIX_73219" in texts
            watermark = json.loads((store.transcript_store.session_dir / "ltm_watermark.json").read_text(encoding="utf-8"))
            assert store.current_message_ids()[-1] in watermark.values()
            return {"durable_rule_written": True, "watermark_advanced": True}

        async def runtime_check():
            settings = workspace / "settings.json"
            settings.write_text('{"retries": 0, "timeout": 30}\n', encoding="utf-8")
            runtime = app.build_runtime(
                workspace, mcp_trust_mode="skip",
                permission_prompter=FixturePermissionPrompter(workspace),
            )
            runtime.state.max_turns = 5
            runtime.state.metadata["disabled_tools"] = {
                descriptor.name for descriptor in runtime.base_descriptors
                if descriptor.name not in {"read_file", "edit_file", "write_file"}
            } | {"agent"}
            try:
                status = None
                async for event in runtime.loop.stream(
                    "Fix settings.json: retries must be 3. Read it first, preserve timeout=30, "
                    "edit only that file, then read it back to verify. Do not ask questions."
                ):
                    if event.type == "completed":
                        status = event.metadata.get("status")
                assert status == "completed"
                assert json.loads(settings.read_text(encoding="utf-8")) == {"retries": 3, "timeout": 30}
                messages = runtime.message_store.current_messages()
                assert any(m.get("role") == "tool_result" for m in messages)
            finally:
                await runtime.close()
                shutil.copytree(runtime.message_store.transcript_store.session_dir, output / "runtime-session")
            assert (output / "runtime-session" / "messages.jsonl").is_file()
            return {"settings_verified": True, "session_closed": True}

        with (
            patch.object(Path, "home", return_value=home),
            patch.dict(os.environ, {"NERVURE_HOME": str(home), "ONECODE_HOME": str(home)}),
            patch.object(app, "create_model_client", return_value=client),
        ):
            for name, operation in (
                ("stream", stream_check), ("tool_roundtrip", tool_check),
                ("memory_selector", selector_check), ("full_compaction", compact_check),
                ("explore_subagent", explore_check), ("memory_extraction", extraction_check),
                ("runtime_file_edit_and_close", runtime_check),
            ):
                if name not in cases:
                    continue
                await case(name, operation)
                if name == "stream" and (
                    not client.calls or client.calls[-1]["status"] != "completed"
                ):
                    break
        store.flush_transcript()
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    save()
    return 0 if results and all(item["status"] == "pass" for item in results) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--config", type=Path, default=Path(".env"))
    parser.add_argument("--model", help="Test-only model override; never rewrites provider configuration.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget", type=int, default=1_000_000)
    parser.add_argument("--cases", nargs="+", choices=CASE_NAMES, default=CASE_NAMES)
    args = parser.parse_args(argv)
    if not args.allow_live:
        parser.error("Real provider calls require --allow-live.")
    config = load_provider_config(args.config.resolve())
    config = replace(config, model=args.model or config.model,
                     model_call_timeout_seconds=min(config.model_call_timeout_seconds, 120),
                     default_params={**config.default_params, "stream_options": {"include_usage": True}})
    client = MeteredClient(OpenAICompatibleChatCompletionsClient(config), budget=args.budget)
    args.output.mkdir(parents=True, exist_ok=False)
    return asyncio.run(run_suite(client, args.output.resolve(), cases=args.cases))


if __name__ == "__main__":
    raise SystemExit(main())
