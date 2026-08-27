"""Workspace-local Nervure state path helpers.

New state is written under ``.nervure``. Existing ``.onecode`` session data
remains readable so a branding migration does not invalidate old workspaces.
"""

from __future__ import annotations

from pathlib import Path


PRIMARY_STATE_DIR = ".nervure"
LEGACY_STATE_DIR = ".onecode"


def nervure_dir(workspace: Path | str) -> Path:
    return Path(workspace) / PRIMARY_STATE_DIR


def legacy_state_dir(workspace: Path | str) -> Path:
    return Path(workspace) / LEGACY_STATE_DIR


def sessions_dir(workspace: Path | str) -> Path:
    return nervure_dir(workspace) / "sessions"


def legacy_sessions_dir(workspace: Path | str) -> Path:
    return legacy_state_dir(workspace) / "sessions"


def session_roots(workspace: Path | str) -> tuple[Path, ...]:
    """Return primary then existing legacy session roots."""

    primary = sessions_dir(workspace)
    legacy = legacy_sessions_dir(workspace)
    if legacy.exists() and legacy != primary:
        return (primary, legacy)
    return (primary,)


def session_dir(workspace: Path | str, session_id: str) -> Path:
    """Use an existing legacy session in place; otherwise use ``.nervure``."""

    primary = sessions_dir(workspace) / session_id
    legacy = legacy_sessions_dir(workspace) / session_id
    if not primary.exists() and legacy.exists():
        return legacy
    return primary


def session_messages_path(workspace: Path | str, session_id: str) -> Path:
    return session_dir(workspace, session_id) / "messages.jsonl"


def session_tool_results_dir(workspace: Path | str, session_id: str) -> Path:
    return session_dir(workspace, session_id) / "tool-results"


def session_background_tasks_dir(workspace: Path | str, session_id: str) -> Path:
    return session_dir(workspace, session_id) / "background-tasks"


def existing_state_dir(workspace: Path | str) -> Path:
    """Read existing primary state first, then legacy state, else primary."""

    primary = nervure_dir(workspace)
    legacy = legacy_state_dir(workspace)
    if primary.exists() or not legacy.exists():
        return primary
    return legacy
