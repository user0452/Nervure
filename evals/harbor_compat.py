"""Optional Harbor boundary.

No normal Nervure import depends on Harbor.  This module centralizes the
optional import and the tested Harbor release so API drift produces a clear
error instead of a partial evaluation.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

HARBOR_VERSION = "0.21.0"
HARBOR_TAG = "v0.21.0"
HARBOR_COMMIT = "64afbbcb62165950301e1a6407c729aa26d844ff"


class HarborUnavailableError(RuntimeError):
    """Raised when an eval command needs the optional Harbor extra."""


def require_harbor() -> Any:
    try:
        return import_module("harbor")
    except ImportError as exc:
        raise HarborUnavailableError(
            "Harbor is not installed. Install the optional eval dependency with "
            "`python -m pip install -e \".[eval]\"` (Python 3.12+), then retry."
        ) from exc


def harbor_trajectory_validator() -> Any:
    require_harbor()
    try:
        return import_module("harbor.utils.trajectory_validator").TrajectoryValidator
    except (ImportError, AttributeError) as exc:
        raise HarborUnavailableError(
            f"Installed Harbor does not expose the validated ATIF API expected by "
            f"Nervure ({HARBOR_TAG})."
        ) from exc


def harbor_base_installed_agent() -> tuple[type[Any], Any]:
    require_harbor()
    try:
        module = import_module("harbor.agents.installed.base")
        return module.BaseInstalledAgent, module.with_prompt_template
    except (ImportError, AttributeError) as exc:
        raise HarborUnavailableError(
            f"Installed Harbor does not expose BaseInstalledAgent for {HARBOR_TAG}."
        ) from exc
