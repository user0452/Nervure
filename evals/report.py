"""Human-readable reports layered on top of Harbor results."""

from __future__ import annotations

import json
from typing import Any


def render_trial_report(metrics: dict[str, Any], *, atif_valid: bool | None, revision: str) -> str:
    main = metrics.get("main_agent", {})
    child = metrics.get("child_agent", {})
    total = metrics.get("total", {})
    tools = metrics.get("tools", {})
    memory = metrics.get("memory", {})
    subagent = metrics.get("subagent", {})
    return "\n".join(
        [
            "# Nervure Eval Trial",
            "",
            f"- Revision: `{revision}`",
            f"- ATIF valid: `{atif_valid}`",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| Model calls | {main.get('model_calls', 0)} |",
            f"| Child model calls | {child.get('model_calls', 0)} |",
            f"| Total model calls | {total.get('model_calls', main.get('model_calls', 0))} |",
            f"| Input tokens | {main.get('input_tokens', 0)} |",
            f"| Cache-read tokens | {main.get('cache_read_input_tokens', 0)} |",
            f"| Uncached tokens | {main.get('uncached_input_tokens', 0)} |",
            f"| Output tokens | {main.get('output_tokens', 0)} |",
            f"| Reasoning tokens | {main.get('reasoning_tokens', 0)} |",
            f"| Duration (ms) | {main.get('duration_ms', 0)} |",
            f"| Tool calls | {tools.get('tool_calls', 0)} |",
            f"| Tool errors | {tools.get('tool_errors', 0)} |",
            f"| Subagent started / completed / errors | {subagent.get('child_count', 0)} / {subagent.get('completed_count', 0)} / {subagent.get('error_count', 0)} |",
            f"| Child duration (ms) | {subagent.get('child_duration_ms', 0)} |",
            f"| Selector fresh calls | {memory.get('selector_fresh_calls', 0)} |",
            f"| Selector cache hits | {memory.get('selector_cache_hits', 0)} |",
            f"| Selector parse failures | {memory.get('selector_parse_failures', 0)} |",
            "",
            "Raw trace and Harbor's native result remain the source of truth; this report is a summary.",
            "",
        ]
    )
