"""Provider-visible meta-tool for deferred schema discovery."""

from __future__ import annotations

import json
from typing import Any

from services.tools.types import (
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolResultPolicy,
    ToolRuntime,
    ToolTarget,
    ValidationResult,
)
from tools.tool_search.prompt import PROMPT


INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Capability or task to search for among deferred tools.",
        }
    },
    "required": ["query"],
    "additionalProperties": False,
}


def descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        name="tool_search",
        description="Search allowed deferred tools and load matching schemas for the next turn.",
        input_schema=INPUT_SCHEMA,
        handler=_handle,
        prompt=PROMPT,
        search_hint="search deferred tool capabilities",
        validate_input=_validate,
        classify_input=_classify,
    )


def _validate(tool_input: dict[str, Any], runtime: ToolRuntime) -> ValidationResult:
    query = tool_input.get("query")
    if not isinstance(query, str) or not query.strip():
        return ValidationResult.failure("query must be a non-empty string.")
    return ValidationResult.success()


def _classify(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ToolCallClassification:
    return ToolCallClassification(
        read_only=True,
        modifies_filesystem=False,
        concurrency_safe=True,
        targets=(
            ToolTarget(
                kind="session_state",
                operation="tool_search",
                value=runtime.state.session_id,
            ),
        ),
        result_policy=ToolResultPolicy(max_result_size_chars=20_000),
        permission_subject="tool_search",
    )


def _handle(tool_input: dict[str, Any], runtime: ToolRuntime) -> ToolExecutionResult:
    registry = runtime.registry
    if registry is None:
        return ToolExecutionResult(
            tool_call_id=runtime.tool_call_id,
            tool_name="tool_search",
            content="Tool search is unavailable because the runtime registry is not bound.",
            is_error=True,
            metadata={"error": "tool_search_registry_unavailable"},
        )

    selection = registry.search_and_load(runtime.state, str(tool_input["query"]))
    if not selection.deferred_mode:
        payload = {
            "deferred_mode": False,
            "tools": [],
            "message": "All allowed normal tools are already directly available.",
        }
    elif not selection.candidates:
        payload = {
            "deferred_mode": True,
            "tools": [],
            "message": "No allowed deferred tools matched this query.",
        }
    else:
        payload = {
            "deferred_mode": True,
            "tools": [
                {"name": item.name, "description": item.description}
                for item in selection.candidates
            ],
            "message": "Matching tool schemas are now available on the next model turn.",
        }
    payload["schema_budget_reached"] = selection.schema_budget_reached
    return ToolExecutionResult(
        tool_call_id=runtime.tool_call_id,
        tool_name="tool_search",
        content=json.dumps(payload, ensure_ascii=False),
        metadata=payload,
    )
