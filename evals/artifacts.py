"""Small artifact helpers; Harbor remains the lifecycle owner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def git_metadata(repository_root: Path) -> dict[str, Any]:
    import subprocess

    def run(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", *args], cwd=repository_root, capture_output=True,
                text=True, check=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        return result.stdout.strip() or None

    return {
        "repository_root": str(repository_root),
        "revision": run("rev-parse", "HEAD"),
        "branch": run("branch", "--show-current"),
        "dirty": run("status", "--porcelain") is not None,
    }
