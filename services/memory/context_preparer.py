"""Context preparer that appends selected long-term memory attachments."""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Iterable, Protocol

from core.runtime_state import RuntimeState
from services.context.snapshot import PreparedContext
from services.memory.auto_store import LongTermMemoryStore
from services.memory.selector import RelevantMemorySelector, catalog_fingerprint
from services.memory.types import LongTermMemoryFile
from services.observability import TraceRecorder


class InnerContextPreparer(Protocol):
    def prepare(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
    ) -> (
        Iterable[dict[str, Any]]
        | PreparedContext
        | Awaitable[Iterable[dict[str, Any]] | PreparedContext]
    ):
        ...


@dataclass(frozen=True)
class _SelectionCache:
    session_id: str
    user_turn_id: str
    catalog_fingerprint: str
    selected: tuple[LongTermMemoryFile, ...]


class RelevantMemoryContextPreparer:
    def __init__(
        self,
        store: LongTermMemoryStore,
        selector: RelevantMemorySelector,
        *,
        inner: InnerContextPreparer | None = None,
        max_total_chars: int = 60_000,
        trace_recorder: TraceRecorder | None = None,
    ) -> None:
        self.store = store
        self.selector = selector
        self.inner = inner
        self.max_total_chars = max_total_chars
        self._trace_recorder = trace_recorder or TraceRecorder.noop()
        self._selection_cache: _SelectionCache | None = None

    async def prepare(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
    ) -> PreparedContext:
        prepared: Iterable[dict[str, Any]] | PreparedContext
        if self.inner is None:
            prepared = messages
        else:
            prepared = self.inner.prepare(messages, state)
            if inspect.isawaitable(prepared):
                prepared = await prepared
        usage_hints: dict[str, Any] = {}
        transcript_refs: tuple[str, ...] = ()
        if isinstance(prepared, PreparedContext):
            prepared_messages = tuple(prepared.messages)
            usage_hints = dict(prepared.usage_hints)
            transcript_refs = tuple(prepared.transcript_refs)
        else:
            prepared_messages = tuple(prepared)
        memory_messages = await self._memory_attachments(prepared_messages, state)
        if memory_messages:
            usage_hints["relevant_memory_count"] = len(memory_messages)
        return PreparedContext(
            messages=(*prepared_messages, *memory_messages),
            usage_hints=usage_hints,
            transcript_refs=transcript_refs,
        )

    async def _memory_attachments(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
    ) -> tuple[dict[str, Any], ...]:
        catalog = self.store.scan()
        fingerprint = catalog_fingerprint(catalog)
        user_turn_id = _user_turn_identity(messages, state)
        cache = self._selection_cache
        if (
            cache is not None
            and cache.session_id == state.session_id
            and cache.user_turn_id == user_turn_id
            and cache.catalog_fingerprint == fingerprint
        ):
            selected = cache.selected
            self._trace_recorder.event(
                "long_term_memory_selector_cache_hit",
                {
                    "user_turn_id": user_turn_id,
                    "catalog_count": len(catalog),
                    "catalog_fingerprint": fingerprint,
                    "selected_count": len(selected),
                    "selected_paths": tuple(
                        item.relative_path for item in selected
                    ),
                    "cache_hit": True,
                },
            )
        else:
            selected = await self.selector.select(messages, state, catalog)
            self._selection_cache = _SelectionCache(
                session_id=state.session_id,
                user_turn_id=user_turn_id,
                catalog_fingerprint=fingerprint,
                selected=tuple(selected),
            )
        attachments: list[dict[str, Any]] = []
        total_chars = 0
        surfaced: list[str] = []
        for memory in selected:
            content = self.store.read_topic(memory.relative_path)
            if total_chars + len(content) > self.max_total_chars:
                content = content[: max(0, self.max_total_chars - total_chars)].rstrip()
                content += "\n[Memory attachment budget reached]"
            total_chars += len(content)
            attachments.append(
                {
                    "role": "attachment",
                    "attachment": {
                        "type": "relevant_memories",
                        "path": memory.relative_path,
                        "content": content,
                    },
                    "metadata": {
                        "synthetic": True,
                        "source": "long_term_memory",
                    },
                }
            )
            surfaced.append(memory.relative_path)
            if total_chars >= self.max_total_chars:
                break
        if surfaced:
            state.metadata["long_term_memory_surface_paths"] = surfaced
        return tuple(attachments)


def _user_turn_identity(messages: tuple[dict[str, Any], ...], state: RuntimeState) -> str:
    explicit = getattr(state, "user_turn_id", None)
    if isinstance(explicit, str) and explicit:
        return explicit
    # Direct ContextPreparer callers and older integrations may not enter via
    # AgentLoop.stream(). Derive a bounded fallback from the latest user
    # message so tool-result-only updates remain in the same turn.
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.get("role") == "user":
            content = repr(message.get("content", ""))
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
            return f"implicit:{index}:{digest}"
    return f"session:{state.session_id}:no-user"
