"""Harbor Installed Agent adapter for the current Nervure checkout."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shlex
import shutil
import tempfile
from typing import Any

try:  # Optional dependency boundary: normal Nervure imports do not need Harbor.
    from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
    _HARBOR_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised in optional-dependency tests
    BaseInstalledAgent = object  # type: ignore[assignment,misc]
    _HARBOR_AVAILABLE = False

    def with_prompt_template(function):
        return function


def _install_command() -> str:
    pip_install = (
        "python -m pip install --disable-pip-version-check --no-cache-dir "
        "-c /opt/nervure/evals/runtime-constraints.txt -e /opt/nervure"
    )
    return (
        "for attempt in 1 2 3; do "
        f"{pip_install} && exit 0; "
        "if [ \"$attempt\" -lt 3 ]; then sleep 2; fi; "
        "done; exit 1"
    )


def _prepare_logs_command() -> str:
    return (
        "mkdir -p /logs/agent/nervure && "
        "chmod 777 /logs/agent /logs/agent/nervure"
    )


class NervureAgent(BaseInstalledAgent):
    """Run the real Nervure composition root in Harbor's task workspace."""

    SUPPORTS_ATIF = False
    SUPPORTS_WINDOWS = False

    def __init__(
        self,
        logs_dir: Path | None = None,
        model_name: str | None = None,
        source_root: str | Path | None = None,
        revision: str | None = None,
        timeout_seconds: int = 900,
        extra_env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        self._source_root = Path(source_root).expanduser().resolve() if source_root else None
        self._revision = revision or "working-tree"
        self._timeout_seconds = int(timeout_seconds)
        if _HARBOR_AVAILABLE:
            super().__init__(
                logs_dir=logs_dir or Path("/logs/agent"),
                model_name=model_name,
                extra_env=extra_env,
                **kwargs,
            )
        else:
            self.logs_dir = logs_dir or Path("/logs/agent")
            self.model_name = model_name
            self._extra_env = dict(extra_env or {})

    @staticmethod
    def name() -> str:
        return "nervure"

    def version(self) -> str | None:
        return self._revision

    async def install(self, environment) -> None:
        if self._source_root is None or not self._source_root.is_dir():
            raise RuntimeError("NervureAgent requires a valid source_root agent kwarg.")
        with tempfile.TemporaryDirectory(prefix="nervure-eval-source-") as staging:
            staged = Path(staging) / "nervure"
            shutil.copytree(
                self._source_root,
                staged,
                ignore=shutil.ignore_patterns(
                    ".git", ".venv", "__pycache__", ".onecode", "jobs", "outputs", ".env",
                ),
            )
            await environment.upload_dir(staged, "/opt/nervure")
        await self.exec_as_root(
            environment,
            command=_install_command(),
            timeout_sec=600,
        )

    @with_prompt_template
    async def run(self, instruction: str, environment, context) -> None:
        await self.exec_as_root(
            environment,
            command=_prepare_logs_command(),
        )
        command = (
            "python -m evals.headless "
            "--workspace /workspace "
            f"--timeout {self._timeout_seconds} "
            f"--prompt {shlex.quote(instruction)}"
        )
        env = {
            "NERVURE_TRACE_LEVEL": "debug",
            "NERVURE_MAX_TOOL_RESULT_CHARS": "12000",
            "NERVURE_EVAL_REVISION": self._revision,
            "NERVURE_EVAL_MODEL": self.model_name or "",
        }
        # Harbor's --agent-env values arrive in ``extra_env`` and are scoped by
        # Trial around Agent commands.  Include the ablation flag explicitly in
        # this command env as well so a custom Harbor runner or a direct agent
        # invocation cannot silently drop it before ``build_runtime`` reads it.
        for switch_name in (
            "NERVURE_DISABLE_REPO_MAP",
            "NERVURE_DISABLE_SYMBOL_SEARCH",
            "NERVURE_DISABLE_SUBAGENT",
            "NERVURE_DISABLE_AGENT_TOOL",
        ):
            switch_value = self._extra_env.get(switch_name)
            if switch_value is None:
                switch_value = os.environ.get(switch_name)
            if switch_value is not None:
                env[switch_name] = switch_value
        await self.exec_as_agent(
            environment,
            command=command,
            env=env,
            cwd="/workspace",
            timeout_sec=self._timeout_seconds,
        )

    def populate_context_post_run(self, context) -> None:
        """Expose deterministic token totals without making Harbor parse Nervure."""

        trace_path = self.logs_dir / "nervure" / "trace.jsonl"
        if not trace_path.exists():
            return
        from evals.metrics import NervureTraceAnalyzer
        from evals.trace_atif import NervureTraceToATIFConverter

        output_dir = self.logs_dir / "nervure"
        metrics = NervureTraceAnalyzer().analyze_file(trace_path)
        (output_dir / "nervure_metrics.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        conversion = NervureTraceToATIFConverter(agent_version=self._revision).convert_file(trace_path)
        (output_dir / "trajectory.json").write_text(
            json.dumps(conversion.trajectory, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (output_dir / "atif_validation.json").write_text(
            json.dumps(
                {"atif_valid": conversion.atif_valid, "errors": conversion.errors},
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        from evals.report import render_trial_report
        (output_dir / "nervure_report.md").write_text(
            render_trial_report(metrics, atif_valid=conversion.atif_valid, revision=self._revision),
            encoding="utf-8",
        )
        if context is not None:
            context.metadata = {
                **(context.metadata or {}),
                "nervure_revision": self._revision,
                "nervure_metrics": metrics,
                "atif_valid": conversion.atif_valid,
            }
            context.n_input_tokens = metrics["main_agent"]["input_tokens"]
            context.n_cache_tokens = metrics["main_agent"]["cache_read_input_tokens"]
            context.n_output_tokens = metrics["main_agent"]["output_tokens"]
