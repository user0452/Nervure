"""Small file-snapshot checkpoint store; intentionally not a Git replacement."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import base64
import json
from pathlib import Path
import uuid


@dataclass(frozen=True)
class Checkpoint:
    id: str
    session_id: str
    timestamp: str
    tool_call_id: str
    tool_name: str
    files: tuple[str, ...]
    path: Path


class CheckpointRestoreError(RuntimeError):
    pass


class CheckpointStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def create(self, *, session_id: str, tool_call_id: str, tool_name: str, paths: tuple[Path, ...]) -> Checkpoint:
        checkpoint_id = uuid.uuid4().hex
        directory = self.root / session_id / checkpoint_id
        directory.mkdir(parents=True, exist_ok=False)
        entries: list[dict[str, object]] = []
        for index, path in enumerate(dict.fromkeys(path.resolve(strict=False) for path in paths)):
            item: dict[str, object] = {"path": str(path), "exists": path.exists(), "is_file": path.is_file()}
            if path.is_file():
                item["content_b64"] = base64.b64encode(path.read_bytes()).decode("ascii")
            entries.append(item)
        checkpoint = Checkpoint(checkpoint_id, session_id, datetime.now(timezone.utc).isoformat(), tool_call_id, tool_name, tuple(str(item["path"]) for item in entries), directory / "checkpoint.json")
        payload = asdict(checkpoint)
        payload["path"] = str(checkpoint.path)
        payload["entries"] = entries
        checkpoint.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return checkpoint

    def list(self, session_id: str) -> tuple[Checkpoint, ...]:
        directory = self.root / session_id
        if not directory.exists():
            return ()
        result: list[Checkpoint] = []
        for manifest in sorted(directory.glob("*/checkpoint.json"), reverse=True):
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                result.append(Checkpoint(
                    id=str(payload["id"]), session_id=str(payload["session_id"]), timestamp=str(payload["timestamp"]),
                    tool_call_id=str(payload["tool_call_id"]), tool_name=str(payload["tool_name"]),
                    files=tuple(str(item) for item in payload["files"]), path=manifest,
                ))
            except (OSError, KeyError, TypeError, json.JSONDecodeError):
                continue
        return tuple(result)

    def restore(self, checkpoint_id: str, *, session_id: str, confirmed: bool = False) -> Checkpoint:
        if not confirmed:
            raise CheckpointRestoreError("Checkpoint restore requires explicit confirmation.")
        manifest = self.root / session_id / checkpoint_id / "checkpoint.json"
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            entries = payload["entries"]
            if not isinstance(entries, list):
                raise ValueError("entries is not a list")
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CheckpointRestoreError(f"Checkpoint is unavailable or corrupted: {checkpoint_id}") from exc
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise CheckpointRestoreError(f"Checkpoint is corrupted: {checkpoint_id}")
            path = Path(entry["path"])
            if entry.get("exists") is True:
                if entry.get("is_file") is not True or not isinstance(entry.get("content_b64"), str):
                    raise CheckpointRestoreError(f"Unsupported checkpoint entry: {path}")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(base64.b64decode(entry["content_b64"], validate=True))
            elif path.exists():
                if path.is_dir():
                    raise CheckpointRestoreError(f"Refusing to remove directory during restore: {path}")
                path.unlink()
        checkpoint = next((item for item in self.list(session_id) if item.id == checkpoint_id), None)
        if checkpoint is None:
            raise CheckpointRestoreError(f"Checkpoint is unavailable or corrupted: {checkpoint_id}")
        return checkpoint
