"""Path helpers for workspace-local long-term memory."""

from __future__ import annotations

from pathlib import Path

from infrastructure.filesystem.paths import resolve_path
from services.memory.types import MemoryPaths


def memory_paths(workspace: Path | str) -> MemoryPaths:
    workspace_path = resolve_path(Path(workspace))
    primary = workspace_path / ".nervure" / "memory"
    legacy = workspace_path / ".onecode" / "memory"
    memory_dir = primary if primary.exists() or not legacy.exists() else legacy
    return MemoryPaths(
        workspace=workspace_path,
        memory_dir=memory_dir,
        entrypoint=memory_dir / "MEMORY.md",
    )


def is_auto_memory_path(path: Path | str, workspace: Path | str) -> bool:
    target = resolve_path(Path(path))
    workspace_path = resolve_path(Path(workspace))
    for memory_dir in (
        workspace_path / ".nervure" / "memory",
        workspace_path / ".onecode" / "memory",
    ):
        try:
            target.relative_to(memory_dir)
        except ValueError:
            continue
        return True
    return False


def is_auto_memory_markdown_path(path: Path | str, workspace: Path | str) -> bool:
    target = resolve_path(Path(path))
    if not is_auto_memory_path(target, workspace):
        return False
    return target.suffix.lower() == ".md"


def normalize_memory_path(path: Path | str, workspace: Path | str) -> Path:
    target = Path(path)
    if not target.is_absolute():
        target = memory_paths(workspace).memory_dir / target
    return resolve_path(target)
