"""Shared types for context compaction."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class CompactionTrigger(StrEnum):
    MICRO = "micro"
    AUTO_FULL = "auto_full"
    MANUAL = "manual"
    REACTIVE = "reactive"


@dataclass(frozen=True)
class CompactionConfig:
    context_window_tokens: int = 128_000
    compact_trigger_ratio: float = 0.80
    compact_summary_reserve_ratio: float = 0.10
    compact_safety_buffer_ratio: float = 0.05
    compact_recent_tail_ratio: float = 0.08
    recent_tail_min_tokens: int = 4_000
    recent_tail_max_tokens: int = 32_000
    tool_result_budget_chars: int = 200_000
    tool_result_preview_chars: int = 4_000
    microcompact_keep_recent: int = 5
    snip_max_messages: int = 80
    max_consecutive_auto_compact_failures: int = 3
    max_reactive_compact_retries: int = 1

    def __post_init__(self) -> None:
        if self.context_window_tokens <= 0:
            raise ValueError("context_window_tokens must be positive")
        if self.recent_tail_min_tokens < 0:
            raise ValueError("recent_tail_min_tokens must be non-negative")
        if self.recent_tail_max_tokens < self.recent_tail_min_tokens:
            raise ValueError(
                "recent_tail_max_tokens must be >= recent_tail_min_tokens"
            )
        ratios = {
            "compact_trigger_ratio": self.compact_trigger_ratio,
            "compact_summary_reserve_ratio": self.compact_summary_reserve_ratio,
            "compact_safety_buffer_ratio": self.compact_safety_buffer_ratio,
            "compact_recent_tail_ratio": self.compact_recent_tail_ratio,
        }
        for name, value in ratios.items():
            if not 0 < value < 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if (
            self.compact_trigger_ratio
            + self.compact_summary_reserve_ratio
            + self.compact_safety_buffer_ratio
            > 1
        ):
            raise ValueError(
                "compact trigger, summary reserve, and safety ratios must total <= 1"
            )

    @property
    def compact_trigger_tokens(self) -> int:
        return int(self.context_window_tokens * self.compact_trigger_ratio)

    @property
    def compact_summary_reserve_tokens(self) -> int:
        return int(self.context_window_tokens * self.compact_summary_reserve_ratio)

    @property
    def compact_safety_buffer_tokens(self) -> int:
        return int(self.context_window_tokens * self.compact_safety_buffer_ratio)

    @property
    def recent_tail_budget_tokens(self) -> int:
        derived = int(self.context_window_tokens * self.compact_recent_tail_ratio)
        return min(self.recent_tail_max_tokens, max(self.recent_tail_min_tokens, derived))

    @property
    def auto_compact_threshold_tokens(self) -> int:
        """Compatibility name for the ratio-derived compact trigger."""

        return self.compact_trigger_tokens


@dataclass(frozen=True)
class CompactBoundary:
    boundary_id: str
    trigger: CompactionTrigger
    token_before: int
    token_after: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CompactionResult:
    trigger: CompactionTrigger
    messages: tuple[dict[str, Any], ...]
    token_before: int
    token_after: int
    transcript_refs: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
