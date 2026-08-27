"""Deterministic, read-only Python AST definition search."""

from __future__ import annotations

import ast
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from services.tools.types import (
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolResultPolicy,
    ToolRuntime,
    ToolTarget,
    ValidationResult,
    is_guard_policy_allowed,
)
from tools.symbol_search.prompt import PROMPT


DEFAULT_MAX_RESULTS = 20
MAX_MAX_RESULTS = 100
MAX_FILES = 500
MAX_OUTPUT_CHARS = 10_000
MAX_SIGNATURE_CHARS = 180
MAX_WARNING_LINES = 30

EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git",
        ".nervure",
        ".onecode",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        "build",
        "dist",
        "cache",
        ".cache",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
    }
)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Symbol name or qualified name to locate.",
        },
        "path": {
            "type": "string",
            "description": (
                "Optional workspace-relative directory to search; defaults to the workspace root."
            ),
        },
        "max_results": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_MAX_RESULTS,
            "description": f"Optional result limit; defaults to {DEFAULT_MAX_RESULTS}.",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class _SearchInput:
    query: str
    path: str = "."
    max_results: int = DEFAULT_MAX_RESULTS


@dataclass(frozen=True)
class _SymbolMatch:
    relative_path: str
    line: int
    kind: str
    name: str
    qualified_name: str
    signature: str
    rank: int


@dataclass
class _SearchResult:
    query: str
    files_seen: int = 0
    files_included: int = 0
    warnings: list[str] = field(default_factory=list)
    matches: list[_SymbolMatch] = field(default_factory=list)
    files_truncated: bool = False
    results_truncated: bool = False
    output_truncated: bool = False


def descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        name="symbol_search",
        description=(
            "Locate a known or suspected Python class, function, or method definition "
            "before focused reading; use repo_map for broad orientation and grep for text/usages."
        ),
        input_schema=INPUT_SCHEMA,
        handler=_handle,
        prompt=PROMPT,
        search_hint="locate a known Python symbol definition",
        validate_input=_validate,
        classify_input=_classify_input,
    )


def _validate(tool_input: dict[str, Any], runtime: ToolRuntime) -> ValidationResult:
    query = tool_input.get("query")
    if not isinstance(query, str) or not query.strip():
        return ValidationResult.failure("query must be a non-empty string.")
    path = tool_input.get("path", ".")
    if not isinstance(path, str) or not path.strip():
        return ValidationResult.failure("path must be a non-empty string when provided.")
    max_results = tool_input.get("max_results", DEFAULT_MAX_RESULTS)
    if isinstance(max_results, bool) or not isinstance(max_results, int):
        return ValidationResult.failure("max_results must be an integer.")
    if max_results < 1 or max_results > MAX_MAX_RESULTS:
        return ValidationResult.failure(
            f"max_results must be between 1 and {MAX_MAX_RESULTS}."
        )
    return ValidationResult.success()


def _classify_input(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ToolCallClassification:
    path = str(tool_input.get("path", "."))
    return ToolCallClassification(
        read_only=True,
        modifies_filesystem=False,
        concurrency_safe=True,
        targets=(ToolTarget(kind="directory", operation="list", value=path),),
        result_policy=ToolResultPolicy(
            max_result_size_chars=MAX_OUTPUT_CHARS,
            persist_when_exceeded=False,
            preview_chars=4_000,
        ),
        permission_subject=f"symbol_search:{path}",
    )


def _handle(tool_input: dict[str, Any], runtime: ToolRuntime) -> ToolExecutionResult:
    if runtime.guard is None:
        raise RuntimeError("symbol_search requires a sandbox guard.")

    parsed = _SearchInput(
        query=str(tool_input.get("query", "")).strip(),
        path=str(tool_input.get("path", ".")),
        max_results=int(tool_input.get("max_results", DEFAULT_MAX_RESULTS)),
    )
    root_policy = runtime.guard.check_path(
        parsed.path,
        operation="list",
        kind="directory",
    )
    if not is_guard_policy_allowed(root_policy, runtime):
        return _guard_error(root_policy)

    root = root_policy.normalized_path
    if not root.exists():
        return _error_result(
            "directory_not_found",
            "The selected repository path does not exist.",
        )
    if not root.is_dir():
        return _error_result(
            "path_not_directory",
            "The selected repository path is not a directory.",
        )

    workspace = runtime.guard.boundary.cwd.resolve(strict=False)
    result = _scan(parsed, root, workspace, runtime)
    result.matches.sort(key=lambda item: (item.rank, item.relative_path.casefold(), item.relative_path, item.line, item.qualified_name))
    if len(result.matches) > parsed.max_results:
        result.matches = result.matches[: parsed.max_results]
        result.results_truncated = True
    content, output_truncated = _render(result)
    result.output_truncated = output_truncated
    truncation_reasons = tuple(
        reason
        for reason, enabled in (
            ("max_files", result.files_truncated),
            ("max_results", result.results_truncated),
            ("max_output_chars", result.output_truncated),
        )
        if enabled
    )
    metadata = {
        "query": parsed.query,
        "path": _display_path(root, workspace),
        "files_seen": result.files_seen,
        "files_included": result.files_included,
        "match_count": len(result.matches),
        "warning_count": len(result.warnings),
        "truncated": bool(truncation_reasons),
        "truncation_reasons": truncation_reasons,
    }
    return ToolExecutionResult(
        tool_call_id=runtime.tool_call_id,
        tool_name="symbol_search",
        content=content,
        metadata=metadata,
    )


def _scan(
    parsed: _SearchInput,
    root: Path,
    workspace: Path,
    runtime: ToolRuntime,
) -> _SearchResult:
    result = _SearchResult(query=parsed.query)
    if root.name.casefold() in EXCLUDED_DIR_NAMES:
        return result
    for candidate in _iter_python_files(root):
        result.files_seen += 1
        if result.files_seen > MAX_FILES:
            result.files_truncated = True
            break
        relative_path = _relative_path(candidate, workspace)
        try:
            resolved = candidate.resolve(strict=False)
            policy = runtime.guard.check_path(resolved, operation="read", kind="file")
            if not is_guard_policy_allowed(policy, runtime):
                result.warnings.append(f"skipped {relative_path}: path is not readable")
                continue
            tree = ast.parse(candidate.read_text(encoding="utf-8"), filename=relative_path)
        except SyntaxError as exc:
            line = exc.lineno if exc.lineno is not None else "?"
            result.warnings.append(f"skipped {relative_path}: syntax error at line {line}")
            continue
        except (OSError, UnicodeError) as exc:
            result.warnings.append(f"skipped {relative_path}: {type(exc).__name__.lower()}")
            continue

        result.files_included += 1
        result.matches.extend(
            match
            for match in _extract_matches(tree, relative_path, parsed.query)
            if match is not None
        )
    return result


def _iter_python_files(root: Path) -> Iterator[Path]:
    for directory, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        directory_path = Path(directory)
        dirnames[:] = sorted(
            name for name in dirnames if name.casefold() not in EXCLUDED_DIR_NAMES
        )
        for filename in sorted(filenames, key=lambda item: (item.casefold(), item)):
            if filename.casefold().endswith(".py"):
                yield directory_path / filename


def _extract_matches(
    tree: ast.Module,
    relative_path: str,
    query: str,
) -> Iterator[_SymbolMatch | None]:
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            match = _build_match(node, relative_path, query, kind="class", qualified_name=node.name)
            if match is not None:
                yield match
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    kind = "async_method" if isinstance(member, ast.AsyncFunctionDef) else "method"
                    qualified_name = f"{node.name}.{member.name}"
                    match = _build_match(
                        member,
                        relative_path,
                        query,
                        kind=kind,
                        qualified_name=qualified_name,
                    )
                    if match is not None:
                        yield match
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
            match = _build_match(
                node,
                relative_path,
                query,
                kind=kind,
                qualified_name=node.name,
            )
            if match is not None:
                yield match


def _build_match(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    relative_path: str,
    query: str,
    *,
    kind: str,
    qualified_name: str,
) -> _SymbolMatch | None:
    name = node.name
    rank = _match_rank(query, name, qualified_name)
    if rank is None:
        return None
    signature = _format_signature(node, kind)
    return _SymbolMatch(
        relative_path=relative_path,
        line=int(getattr(node, "lineno", 1)),
        kind=kind,
        name=name,
        qualified_name=qualified_name,
        signature=signature,
        rank=rank,
    )


def _match_rank(query: str, name: str, qualified_name: str) -> int | None:
    query = query.strip()
    if query == name or query == qualified_name:
        return 0
    folded = query.casefold()
    candidates = (name.casefold(), qualified_name.casefold())
    if folded in candidates:
        return 1
    normalized_query = _normalize_identifier(query)
    normalized_candidates = tuple(_normalize_identifier(item) for item in (name, qualified_name))
    if normalized_query and normalized_query in normalized_candidates:
        return 1
    if any(candidate.startswith(folded) for candidate in candidates):
        return 2
    if normalized_query and any(candidate.startswith(normalized_query) for candidate in normalized_candidates):
        return 2
    if folded in candidates[0] or folded in candidates[1]:
        return 3
    if normalized_query and any(normalized_query in candidate for candidate in normalized_candidates):
        return 3
    return None


def _normalize_identifier(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def _format_signature(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    kind: str,
) -> str:
    if isinstance(node, ast.ClassDef):
        bases = [_unparse(base) for base in node.bases]
        signature = f"class {node.name}"
        if bases:
            signature += f"({', '.join(bases)})"
        return _truncate(" ".join(signature.split()), MAX_SIGNATURE_CHARS)
    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    args = node.args
    positional = list(args.posonlyargs) + list(args.args)
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    parts: list[str] = []
    for index, argument in enumerate(positional):
        text = _format_argument(argument)
        if defaults[index] is not None:
            text += f" = {_unparse(defaults[index])}"
        parts.append(text)
        if args.posonlyargs and index == len(args.posonlyargs) - 1:
            parts.append("/")
    if args.vararg is not None:
        parts.append("*" + _format_argument(args.vararg))
    elif args.kwonlyargs:
        parts.append("*")
    for argument, default in zip(args.kwonlyargs, args.kw_defaults):
        text = _format_argument(argument)
        if default is not None:
            text += f" = {_unparse(default)}"
        parts.append(text)
    if args.kwarg is not None:
        parts.append("**" + _format_argument(args.kwarg))
    signature = f"{prefix}def {node.name}({', '.join(parts)})"
    if node.returns is not None:
        signature += f" -> {_unparse(node.returns)}"
    return _truncate(" ".join(signature.split()), MAX_SIGNATURE_CHARS)


def _format_argument(argument: ast.arg) -> str:
    text = argument.arg
    if argument.annotation is not None:
        text += f": {_unparse(argument.annotation)}"
    return text


def _render(result: _SearchResult) -> tuple[str, bool]:
    lines = [f"Symbol matches for: {result.query}"]
    if result.files_truncated:
        lines.append(f"[truncated: file budget reached ({MAX_FILES})]")
    if result.warnings:
        lines.append(f"Warnings: {len(result.warnings)}")
        lines.extend(f"- {warning}" for warning in result.warnings[:MAX_WARNING_LINES])
        if len(result.warnings) > MAX_WARNING_LINES:
            lines.append(f"- ... {len(result.warnings) - MAX_WARNING_LINES} more warnings")
    if not result.matches:
        lines.append("(no symbol definitions found)")
    else:
        for index, match in enumerate(result.matches, start=1):
            lines.extend(
                (
                    f"{index}. {match.relative_path}:{match.line}",
                    f"   {match.kind} {match.qualified_name} — {match.signature}",
                )
            )
    if result.results_truncated:
        lines.append("[truncated: max_results reached]")
    return _fit_output(lines)


def _fit_output(lines: list[str]) -> tuple[str, bool]:
    marker = "[truncated: output character budget reached]"
    available = max(0, MAX_OUTPUT_CHARS - len(marker) - 1)
    kept: list[str] = []
    size = 0
    for line in lines:
        addition = len(line) + (1 if kept else 0)
        if size + addition > available:
            kept.append(marker)
            return "\n".join(kept), True
        kept.append(line)
        size += addition
    return "\n".join(kept), False


def _relative_path(path: Path, workspace: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(workspace.resolve(strict=False)).as_posix()
    except ValueError:
        return path.name


def _display_path(path: Path, workspace: Path) -> str:
    try:
        relative = path.resolve(strict=False).relative_to(workspace.resolve(strict=False))
    except ValueError:
        return "."
    return "." if str(relative) == "." else relative.as_posix()


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return "..."


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def _guard_error(policy: Any) -> ToolExecutionResult:
    payload = policy.to_tool_error()
    if policy.action == "ask":
        payload["error"] = "path_guard_ask_required"
    return ToolExecutionResult(
        tool_call_id="",
        tool_name="symbol_search",
        content=json.dumps(payload, ensure_ascii=False),
        is_error=True,
        metadata={"error": payload["error"]},
    )


def _error_result(error: str, message: str) -> ToolExecutionResult:
    payload = {"error": error, "message": message}
    return ToolExecutionResult(
        tool_call_id="",
        tool_name="symbol_search",
        content=json.dumps(payload, ensure_ascii=False),
        is_error=True,
        metadata={"error": error},
    )
