"""Deterministic metadata search for deferred tool schemas."""

from __future__ import annotations

import re

from services.tools.types import ToolDescriptor


class ToolDiscovery:
    """Rank descriptors using only lightweight provider-safe metadata.

    This class deliberately does not decide which tools a user prompt sees.
    ``ToolRegistry`` owns exposure policy and calls this retriever only from
    the explicit ``tool_search`` tool after applying runtime visibility rules.
    """

    def rank(
        self,
        descriptors: tuple[ToolDescriptor, ...],
        query: str,
    ) -> tuple[ToolDescriptor, ...]:
        terms = _query_terms(query)
        if not terms:
            return ()

        scored: list[tuple[int, str, ToolDescriptor]] = []
        for descriptor in descriptors:
            score = _score(descriptor, terms)
            if score > 0:
                scored.append((score, descriptor.name, descriptor))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return tuple(item[2] for item in scored)


def _query_terms(query: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                term
                for term in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", query.lower())
                if len(term) >= 2
            }
        )
    )


def _score(descriptor: ToolDescriptor, terms: tuple[str, ...]) -> int:
    name = descriptor.name.lower()
    description = descriptor.description.lower()
    hint = descriptor.search_hint.lower()
    score = 0
    for term in terms:
        if term in name:
            score += 6
        if term in description:
            score += 3
        if term in hint:
            score += 2
    return score
