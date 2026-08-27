"""Inline terminal REPL for the TTY CLI path.

The interactive path uses :class:`persistent_app.PersistentTerminalApp` as
one long-lived prompt_toolkit render owner. Its transcript, Activity rows,
assistant streaming text, HITL overlay and input Buffer share one render tree;
agent events update state instead of writing directly to stdout. Batch and
legacy compatibility helpers retain their own plain/static output paths.

- **Alternate screen**: full-screen temporary surfaces (``/status``,
  ``/resume`` selector, permission prompts, MCP trust, ``/connect``
  wizard) enter DEC 1049 before the first frame and exit on ``finally``,
  so their contents never leak into the main scrollback.

The package is built up across the milestones in
``docs/exec-plans/active/cli-inline-terminal-ui-refactor-execplan.md``:

- M0 exposes :mod:`ui.cli.terminal.detect` and the spike module.
- M1 adds :class:`InlineRepl` and the rest of the package.
"""

from __future__ import annotations

from ui.cli.terminal.detect import (
    TerminalBrightness,
    detect_terminal_brightness,
)
from ui.cli.terminal.repl import InlineRepl

__all__ = [
    "InlineRepl",
    "TerminalBrightness",
    "detect_terminal_brightness",
]
