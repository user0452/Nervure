"""Headless entrypoint used inside a Harbor agent environment.

It wires the existing Nervure composition root and AgentLoop.  It does not
implement a second agent loop or answer HITL questions on behalf of a user.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess

from ui.cli.app import build_runtime
from services.permissions import PermissionResponse


class HarborWorkspacePermissionPrompter:
    """Approve interactive permission requests in the non-interactive eval host.

    ``PermissionPolicy`` invokes this only after guard checks and project-level
    denies have run. This replaces the CLI's EOF-as-deny behavior only inside
    a Harbor task container. Path guards and a working directory are not OS
    isolation; this prompter must not be used as a host-machine sandbox.
    """

    async def request_permission(self, request) -> PermissionResponse:
        _ = request
        return PermissionResponse(
            action="allow",
            scope="session",
            feedback="Harbor headless task workspace permission granted.",
        )


async def run_headless(
    prompt: str,
    workspace: Path,
    *,
    timeout_seconds: float = 900.0,
) -> int:
    runtime = build_runtime(
        workspace,
        mcp_trust_mode="skip",
        allow_provider_process_env=True,
        permission_prompter=HarborWorkspacePermissionPrompter(),
    )
    try:
        attachments = ()
        if runtime.attachment_collector is not None:
            attachments = await runtime.attachment_collector.collect_for_user_turn(
                prompt,
                runtime.state,
                runtime.message_store.current_messages(),
                is_main_thread=True,
            )
        final_text = ""
        final_text = await asyncio.wait_for(
            _consume_stream(runtime, prompt, attachments), timeout=timeout_seconds
        )
        if final_text:
            print(final_text)
        return 0
    finally:
        await _shutdown(runtime, workspace)


async def _consume_stream(runtime, prompt: str, attachments) -> str:
    final_text = ""
    completed = False
    async for event in runtime.loop.stream(prompt, attachments=attachments):
        if event.type in {"error", "suspended"}:
            raise RuntimeError(f"Nervure eval did not complete: {event.type}.")
        if event.type == "completed":
            status = event.metadata.get("status", "completed")
            if status != "completed":
                raise RuntimeError(f"Nervure eval did not complete: {status}.")
            completed = True
            final_text = event.text
    if not completed:
        raise RuntimeError("Nervure eval did not complete: stream ended without completion.")
    return final_text


async def _shutdown(runtime, workspace: Path) -> None:
    await runtime.close()
    log_dir = Path("/logs/agent/nervure")
    log_dir.mkdir(parents=True, exist_ok=True)
    trace_path = runtime.trace_recorder.trace_path
    if trace_path is not None and trace_path.exists():
        shutil.copyfile(trace_path, log_dir / "trace.jsonl")
    _write_git_diff(workspace, log_dir / "git_diff.patch")
    (log_dir / "task_metadata.json").write_text(
        json.dumps(
            {
                "agent": "nervure",
                "revision": os.environ.get("NERVURE_EVAL_REVISION", "working-tree"),
                "model": os.environ.get("NERVURE_EVAL_MODEL"),
                "trace_level": "debug",
                "workspace": "/workspace",
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


def _write_git_diff(workspace: Path, destination: Path) -> None:
    try:
        result = subprocess.run(
            ["git", "diff", "--binary"],
            cwd=workspace,
            capture_output=True,
            text=False,
            check=False,
        )
        destination.write_bytes(result.stdout)
    except OSError as exc:
        destination.write_text(f"git diff unavailable: {exc}\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nervure-headless")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--timeout", type=float, default=float(os.getenv("NERVURE_EVAL_TIMEOUT", "900")))
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run_headless(args.prompt, args.workspace, timeout_seconds=args.timeout))
    except TimeoutError:
        print("Nervure eval timed out.", flush=True)
        return 124


if __name__ == "__main__":
    raise SystemExit(main())
