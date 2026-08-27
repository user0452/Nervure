"""Lazy tool-schema selection without changing the executor's authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from services.tools.types import ToolDescriptor


@dataclass(frozen=True)
class ToolMetadata:
    name: str
    category: str = "general"
    always_visible: bool = False


class ToolDiscovery:
    """Simple lexical retriever for provider-visible schemas.

    The registry keeps every descriptor executable for direct runtime calls;
    discovery only reduces what is rendered into a model request. Empty or
    low-confidence results fall back to the normal tool set.
    """

    def __init__(self, metadata: Iterable[ToolMetadata] = ()) -> None:
        self._metadata = {item.name: item for item in metadata}

    def select(self, descriptors: tuple[ToolDescriptor, ...], query: str | None) -> tuple[ToolDescriptor, ...]:
        if not query or not query.strip():
            return descriptors
        terms = {part.lower() for part in query.split() if len(part.strip()) >= 2}
        if not terms:
            return descriptors
        selected: list[ToolDescriptor] = []
        matched_relevant_tool = False
        for descriptor in descriptors:
            metadata = self._metadata.get(descriptor.name, ToolMetadata(descriptor.name))
            haystack = " ".join((descriptor.name, descriptor.description, descriptor.search_hint, metadata.category)).lower()
            is_match = any(term in haystack for term in terms)
            if is_match:
                matched_relevant_tool = True
            if metadata.always_visible or is_match:
                selected.append(descriptor)
        return tuple(selected) if matched_relevant_tool else descriptors

    def metadata_for(self, name: str) -> ToolMetadata:
        return self._metadata.get(name, ToolMetadata(name))
