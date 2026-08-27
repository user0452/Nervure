"""Deterministic case selection shared by CLI and tests."""

from __future__ import annotations

import random

from evals.cases import CASES, SUITES


def select_case_ids(
    *,
    count: int,
    seed: int | None = None,
    project: str | None = None,
) -> tuple[str, ...]:
    candidates = [case.case_id for case in CASES if not project or case.project == project]
    if count < 0:
        raise ValueError("count must be non-negative")
    rng = random.Random(seed)
    if count > len(candidates):
        count = len(candidates)
    return tuple(rng.sample(candidates, count))


def suite_case_ids(name: str) -> tuple[str, ...]:
    try:
        return SUITES[name]
    except KeyError as exc:
        raise ValueError(f"Unknown Nervure suite: {name}") from exc
