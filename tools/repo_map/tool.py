"""Deterministic, read-only Python AST repository orientation tool."""

from __future__ import annotations

import ast
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

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
from tools.repo_map.prompt import PROMPT


DEFAULT_MAX_DEPTH = 12
MAX_MAX_DEPTH = 32
MAX_FILES = 500
MAX_SYMBOLS = 2_000
MAX_OUTPUT_CHARS = 30_000
MAX_SIGNATURE_CHARS = 180
MAX_DOCSTRING_CHARS = 120
MAX_WARNING_LINES = 40

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
        "path": {
            "type": "string",
            "description": (
                "Optional workspace-relative directory to scan; defaults to the workspace root."
            ),
        },
        "max_depth": {
            "type": "integer",
            "minimum": 0,
            "description": "Optional directory depth limit relative to path.",
        },
    },
    "additionalProperties": False,
}


@dataclass(frozen=True)
class _RepoMapInput:
    path: str = "."
    max_depth: int = DEFAULT_MAX_DEPTH


@dataclass(frozen=True)
class _FileInfo:
    relative_path: str
    docstring_first_line: str | None
    symbols: tuple[str, ...]


@dataclass
class _TreeNode:
    directories: dict[str, "_TreeNode"] = field(default_factory=dict)
    files: dict[str, _FileInfo] = field(default_factory=dict)


@dataclass
class _ScanResult:
    root: Path
    root_display: str
    files_seen: int = 0
    files_included: int = 0
    symbols_included: int = 0
    warnings: list[str] = field(default_factory=list)
    files: list[_FileInfo] = field(default_factory=list)
    files_truncated: bool = False
    symbols_truncated: bool = False
    output_truncated: bool = False


def descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        name="repo_map",
        description="Build a compact structural map of Python files and symbols in the workspace.",
        input_schema=INPUT_SCHEMA,
        handler=_handle,
        prompt=PROMPT,
        search_hint="orient in an unfamiliar Python repository",
        validate_input=_validate,
        classify_input=_classify_input,
    )


def _validate(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ValidationResult:
    path = tool_input.get("path", ".")
    if not isinstance(path, str) or not path.strip():
        return ValidationResult.failure("path must be a non-empty string when provided.")
    max_depth = tool_input.get("max_depth", DEFAULT_MAX_DEPTH)
    if isinstance(max_depth, bool) or not isinstance(max_depth, int):
        return ValidationResult.failure("max_depth must be an integer.")
    if max_depth < 0 or max_depth > MAX_MAX_DEPTH:
        return ValidationResult.failure(
            f"max_depth must be between 0 and {MAX_MAX_DEPTH}."
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
        permission_subject=f"repo_map:{path}",
    )


def _handle(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ToolExecutionResult:
    if runtime.guard is None:
        raise RuntimeError("repo_map requires a sandbox guard.")

    parsed = _RepoMapInput(
        path=str(tool_input.get("path", ".")),
        max_depth=int(tool_input.get("max_depth", DEFAULT_MAX_DEPTH)),
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
    result = _scan(root, workspace, parsed.max_depth, runtime)
    content = _render(result)
    metadata = {
        "path": result.root_display,
        "files_seen": result.files_seen,
        "files_included": result.files_included,
        "symbols_included": result.symbols_included,
        "warning_count": len(result.warnings),
        "truncated": (
            result.files_truncated
            or result.symbols_truncated
            or result.output_truncated
        ),
        "truncation_reasons": tuple(
            reason
            for reason, enabled in (
                ("max_files", result.files_truncated),
                ("max_symbols", result.symbols_truncated),
                ("max_output_chars", result.output_truncated),
            )
            if enabled
        ),
    }
    return ToolExecutionResult(
        tool_call_id="",
        tool_name="repo_map",
        content=content,
        metadata=metadata,
    )


def _scan(
    root: Path,
    workspace: Path,
    max_depth: int,
    runtime: ToolRuntime,
) -> _ScanResult:
    result = _ScanResult(
        root=root,
        root_display=_display_path(root, workspace),
    )
    if root.name.casefold() in EXCLUDED_DIR_NAMES:
        return result
    for candidate in _iter_python_files(root, max_depth):
        result.files_seen += 1
        if result.files_seen > MAX_FILES:
            result.files_truncated = True
            break

        relative_path = _relative_path(candidate, root)
        try:
            resolved = candidate.resolve(strict=False)
            policy = runtime.guard.check_path(
                resolved,
                operation="read",
                kind="file",
            )
            if not is_guard_policy_allowed(policy, runtime):
                result.warnings.append(f"skipped {relative_path}: path is not readable")
                continue
            source = candidate.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=relative_path)
        except SyntaxError as exc:
            line = exc.lineno if exc.lineno is not None else "?"
            result.warnings.append(f"skipped {relative_path}: syntax error at line {line}")
            continue
        except (OSError, UnicodeError) as exc:
            result.warnings.append(
                f"skipped {relative_path}: {type(exc).__name__.lower()}"
            )
            continue

        symbols = _extract_symbols(tree)
        if result.symbols_included + len(symbols) > MAX_SYMBOLS:
            remaining = max(0, MAX_SYMBOLS - result.symbols_included)
            symbols = symbols[:remaining]
            result.symbols_truncated = True
        result.symbols_included += len(symbols)
        result.files.append(
            _FileInfo(
                relative_path=relative_path,
                docstring_first_line=_docstring_first_line(tree),
                symbols=tuple(symbols),
            )
        )
        result.files_included += 1
        if result.symbols_truncated:
            # The file itself remains useful, but there is no reason to parse
            # additional files once the symbol budget is exhausted.
            break

    return result


def _iter_python_files(root: Path, max_depth: int) -> Iterator[Path]:
    for directory, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        directory_path = Path(directory)
        depth = len(directory_path.relative_to(root).parts)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name.casefold() not in EXCLUDED_DIR_NAMES
        )
        if depth >= max_depth:
            dirnames[:] = []
        for filename in sorted(filenames, key=lambda item: (item.casefold(), item)):
            if filename.casefold().endswith(".py"):
                yield directory_path / filename


def _extract_symbols(tree: ast.Module) -> list[str]:
    symbols: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(_format_callable(node))
        elif isinstance(node, ast.ClassDef):
            symbols.append(f"class {node.name}")
            for member in node.body:
                member_text = _format_class_member(member)
                if member_text:
                    symbols.append(f"  {member_text}")
    return symbols


def _format_class_member(node: ast.AST) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return _format_callable(node)
    if isinstance(node, ast.ClassDef):
        return f"class {node.name}"
    return ""


def _format_callable(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
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
            text += f"={_unparse(default)}"
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


def _docstring_first_line(tree: ast.Module) -> str | None:
    docstring = ast.get_docstring(tree, clean=True)
    if not docstring:
        return None
    return _truncate(" ".join(docstring.splitlines()[0].split()), MAX_DOCSTRING_CHARS)


def _render(result: _ScanResult) -> str:
    lines = [
        f"Repository: {result.root_display}",
        f"Python files: {result.files_included} of {result.files_seen} scanned",
    ]
    if result.files_truncated:
        lines.append(f"[truncated: file budget reached ({MAX_FILES})]")
    if result.symbols_truncated:
        lines.append(f"[truncated: symbol budget reached ({MAX_SYMBOLS})]")
    if result.warnings:
        lines.append(f"Warnings: {len(result.warnings)}")
        lines.extend(f"- {warning}" for warning in result.warnings[:MAX_WARNING_LINES])
        if len(result.warnings) > MAX_WARNING_LINES:
            lines.append(f"- ... {len(result.warnings) - MAX_WARNING_LINES} more warnings")
    lines.append("Tree:")
    if not result.files:
        lines.append("(no readable Python files found)")
    else:
        tree = _build_tree(result.files)
        _render_tree_node(tree, "", lines)

    content, truncated = _fit_output(lines)
    result.output_truncated = truncated
    return content


def _build_tree(files: Iterable[_FileInfo]) -> _TreeNode:
    root = _TreeNode()
    for info in files:
        node = root
        parts = info.relative_path.split("/")
        for directory in parts[:-1]:
            node = node.directories.setdefault(directory, _TreeNode())
        node.files[parts[-1]] = info
    return root


def _render_tree_node(node: _TreeNode, prefix: str, lines: list[str]) -> None:
    entries: list[tuple[str, str, _TreeNode | _FileInfo]] = [
        *(("directory", name, child) for name, child in node.directories.items()),
        *(("file", name, info) for name, info in node.files.items()),
    ]
    entries.sort(key=lambda item: (0 if item[0] == "directory" else 1, item[1].casefold(), item[1]))
    for index, (kind, name, value) in enumerate(entries):
        last = index == len(entries) - 1
        connector = "└─" if last else "├─"
        if kind == "directory":
            lines.append(f"{prefix}{connector} {name}/")
            child_prefix = prefix + ("   " if last else "│  ")
            _render_tree_node(value, child_prefix, lines)  # type: ignore[arg-type]
            continue
        info = value  # type: ignore[assignment]
        file_label = name
        if "/" in info.relative_path:
            file_label += f" (path: {info.relative_path})"
        lines.append(f"{prefix}{connector} {file_label}")
        details = []
        if info.docstring_first_line:
            details.append(f"doc: {info.docstring_first_line}")
        details.extend(symbol for symbol in info.symbols if symbol)
        detail_prefix = prefix + ("   " if last else "│  ")
        for detail_index, detail in enumerate(details):
            detail_last = detail_index == len(details) - 1
            detail_connector = "└─" if detail_last else "├─"
            lines.append(f"{detail_prefix}{detail_connector} {detail}")


def _fit_output(lines: list[str]) -> tuple[str, bool]:
    marker = "[truncated: output character budget reached]"
    if len(marker) >= MAX_OUTPUT_CHARS:
        return marker[:MAX_OUTPUT_CHARS], True
    kept: list[str] = []
    size = 0
    available = MAX_OUTPUT_CHARS - len(marker) - 1
    for line in lines:
        addition = len(line) + (1 if kept else 0)
        if size + addition > available:
            break
        kept.append(line)
        size += addition
    if len(kept) == len(lines):
        return "\n".join(kept), False
    kept.append(marker)
    return "\n".join(kept), True


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _display_path(path: Path, workspace: Path) -> str:
    try:
        relative = path.resolve(strict=False).relative_to(workspace)
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
        tool_name="repo_map",
        content=json.dumps(payload, ensure_ascii=False),
        is_error=True,
        metadata={"error": payload["error"]},
    )


def _error_result(error: str, message: str) -> ToolExecutionResult:
    payload = {"error": error, "message": message}
    return ToolExecutionResult(
        tool_call_id="",
        tool_name="repo_map",
        content=json.dumps(payload, ensure_ascii=False),
        is_error=True,
        metadata={"error": error},
    )
