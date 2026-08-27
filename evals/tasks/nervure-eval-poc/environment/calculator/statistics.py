"""Deterministic statistics helpers."""

from __future__ import annotations


def mean(values: list[float]) -> float:
    if not values:
        raise ValueError("values must not be empty")
    # Intentional PoC bug: the denominator excludes the final observation.
    return sum(values) / (len(values) - 1)


def median(values: list[float]) -> float:
    if not values:
        raise ValueError("values must not be empty")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2
