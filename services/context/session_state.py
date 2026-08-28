"""Lightweight validation metadata for safe session resume decisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Iterable

from core.runtime_state import RuntimeState
from services.memory import InstructionMemoryLoader
from services.tools.registry import ToolRegistry


SESSION_STATE_VERSION = 1
MISSING_FILE_HASH = "<missing>"


class ResumeClassification(StrEnum):
    SAFE_RESUME = "SAFE_RESUME"
    STALE_CONTEXT = "STALE_CONTEXT"
    WORKSPACE_DIVERGED = "WORKSPACE_DIVERGED"


@dataclass(frozen=True)
class SessionValidationMetadata:
    version: int
    workspace: str
    git_head: str | None
    instruction_fingerprint: str
    tool_config_fingerprint: str
    file_hashes: dict[str, str]
    permission_mode: str


@dataclass(frozen=True)
class ResumeValidation:
    classification: ResumeClassification
    reasons: tuple[str, ...] = ()


class SessionStateStore:
    def __init__(self, session_dir: Path | str) -> None:
        self.path = Path(session_dir) / "session_state.json"

    def save(self, metadata: SessionValidationMetadata) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".json.tmp")
        temp_path.write_text(
            json.dumps(asdict(metadata), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.path)

    def load(self) -> SessionValidationMetadata | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != SESSION_STATE_VERSION:
                return None
            file_hashes = payload.get("file_hashes")
            if not isinstance(file_hashes, dict):
                return None
            return SessionValidationMetadata(
                version=SESSION_STATE_VERSION,
                workspace=str(payload["workspace"]),
                git_head=(
                    str(payload["git_head"])
                    if payload.get("git_head") is not None
                    else None
                ),
                instruction_fingerprint=str(payload["instruction_fingerprint"]),
                tool_config_fingerprint=str(payload["tool_config_fingerprint"]),
                file_hashes={str(key): str(value) for key, value in file_hashes.items()},
                permission_mode=str(payload["permission_mode"]),
            )
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            return None


def capture_session_validation_metadata(
    *,
    workspace: Path,
    state: RuntimeState,
    registry: ToolRegistry | None,
    provider_label: str,
    model: str,
    instruction_memory_loader: InstructionMemoryLoader | None,
    extra_paths: Iterable[str | Path] = (),
) -> SessionValidationMetadata:
    workspace = workspace.resolve()
    tracked_paths = _tracked_paths(state, extra_paths)
    instruction_fingerprint = ""
    if instruction_memory_loader is not None:
        instruction_fingerprint = instruction_memory_loader.load(
            state,
            workspace,
        ).fingerprint
    schemas = registry.tool_schemas(state) if registry is not None else ()
    tool_config_fingerprint = _json_fingerprint(
        {
            "provider": provider_label,
            "model": model,
            "tools": schemas,
        }
    )
    return SessionValidationMetadata(
        version=SESSION_STATE_VERSION,
        workspace=str(workspace),
        git_head=_git_head(workspace),
        instruction_fingerprint=instruction_fingerprint,
        tool_config_fingerprint=tool_config_fingerprint,
        file_hashes={
            _stored_path(path, workspace): _file_hash(path)
            for path in tracked_paths
        },
        permission_mode=state.permission_mode.value,
    )


def validate_resume(
    saved: SessionValidationMetadata | None,
    current: SessionValidationMetadata,
) -> ResumeValidation:
    if saved is None:
        return ResumeValidation(
            ResumeClassification.WORKSPACE_DIVERGED,
            ("session_state.json is missing or invalid",),
        )

    diverged: list[str] = []
    stale: list[str] = []
    if saved.workspace != current.workspace:
        diverged.append("workspace path changed")
    if saved.git_head != current.git_head:
        diverged.append("git HEAD changed")
    for stored_path, saved_hash in saved.file_hashes.items():
        path = _resolve_stored_path(stored_path, Path(current.workspace))
        if _file_hash(path) != saved_hash:
            diverged.append(f"file changed: {stored_path}")
    if saved.instruction_fingerprint != current.instruction_fingerprint:
        stale.append("project instructions changed")
    if saved.tool_config_fingerprint != current.tool_config_fingerprint:
        stale.append("model or tool configuration changed")
    if saved.permission_mode != current.permission_mode:
        stale.append("permission mode changed")
    if diverged:
        return ResumeValidation(
            ResumeClassification.WORKSPACE_DIVERGED,
            tuple(diverged + stale),
        )
    if stale:
        return ResumeValidation(ResumeClassification.STALE_CONTEXT, tuple(stale))
    return ResumeValidation(ResumeClassification.SAFE_RESUME)


def stored_file_paths(
    metadata: SessionValidationMetadata | None,
    workspace: Path,
) -> tuple[Path, ...]:
    if metadata is None:
        return ()
    return tuple(
        _resolve_stored_path(path, workspace.resolve())
        for path in metadata.file_hashes
    )


def _tracked_paths(
    state: RuntimeState,
    extra_paths: Iterable[str | Path],
) -> tuple[Path, ...]:
    values: list[str | Path] = list(extra_paths)
    for key in ("files_read", "files_changed"):
        tracked = state.metadata.get(key, ())
        if isinstance(tracked, (set, tuple, list)):
            values.extend(tracked)
    workspace_value = state.metadata.get("workspace")
    workspace = Path(workspace_value) if isinstance(workspace_value, str) else Path.cwd()
    return tuple(
        dict.fromkeys(
            (path if path.is_absolute() else workspace / path).resolve(strict=False)
            for value in values
            if str(value)
            for path in (Path(value),)
        )
    )


def _stored_path(path: Path, workspace: Path) -> str:
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError:
        return str(path)


def _resolve_stored_path(value: str, workspace: Path) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else workspace / path).resolve(strict=False)


def _file_hash(path: Path) -> str:
    if not path.is_file():
        return MISSING_FILE_HASH
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_head(workspace: Path) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    head = completed.stdout.strip()
    return head if completed.returncode == 0 and head else None


def _json_fingerprint(value: object) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()
