"""Long-term memory view."""

from __future__ import annotations

from rich.console import Group
from rich.table import Table

from ui.cli.types import CliRuntime
from ui.cli.views.common import display_path, preview, titled_section


def render_memory(runtime: CliRuntime) -> Group:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="nervure.subtle", no_wrap=True)
    table.add_column()

    store = runtime.long_term_memory_store
    if store is None:
        table.add_row("long-term memory dir", "disabled")
    else:
        topics = store.scan()
        table.add_row("long-term memory dir", display_path(store.memory_dir, runtime.workspace))
        table.add_row(
            "long-term memory index",
            "present" if store.entrypoint_path.exists() else "missing",
        )
        table.add_row("long-term memory topics", str(len(topics)))

    long_extraction = runtime.state.metadata.get("long_term_memory_extraction")
    if isinstance(long_extraction, dict):
        table.add_row(
            "long-term extraction",
            (
                f"{long_extraction.get('last_status', long_extraction.get('last_decision', 'unknown'))} "
                f"running={long_extraction.get('running', False)}"
            ),
        )
    surfaced = runtime.state.metadata.get("long_term_memory_surface_paths")
    if isinstance(surfaced, list) and surfaced:
        table.add_row(
            "recent surfaced memory",
            preview(", ".join(str(item) for item in surfaced[:5])),
        )

    return titled_section("Memory", table, style="nervure.info")
