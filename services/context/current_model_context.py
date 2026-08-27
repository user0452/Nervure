"""Current model-call snapshot holder shared by runtime services."""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy

from services.context.snapshot import ContextSnapshot


@dataclass
class CurrentModelContext:
    snapshot: ContextSnapshot | None = None

    def snapshot_copy(self) -> ContextSnapshot | None:
        """Return an isolated copy of the last model request context.

        ``ContextSnapshot`` is frozen only at the dataclass boundary; its
        provider-facing messages and tool schemas still contain nested dicts.
        Forks must therefore never hand the holder's object graph to a child
        runtime, which may append or otherwise mutate nested request data.
        """

        return deepcopy(self.snapshot) if self.snapshot is not None else None
