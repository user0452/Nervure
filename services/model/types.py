"""Provider-neutral model response types."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from services.errors import ErrorCategory, NervureError
from services.tools.types import ToolCall


class ProviderError(NervureError):
    """Provider-neutral model error raised by infrastructure adapters."""

    def __init__(
        self,
        message: str,
        *,
        provider_id: str | None = None,
        status_code: int | None = None,
        error_type: str | None = None,
        retryable: bool = False,
        retry_after_seconds: float | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        error_metadata: dict[str, Any] = dict(metadata or {})
        if provider_id is not None:
            error_metadata["provider_id"] = provider_id
        if status_code is not None:
            error_metadata["status_code"] = status_code
        if error_type is not None:
            error_metadata["error_type"] = error_type
        if retry_after_seconds is not None:
            error_metadata["retry_after_seconds"] = retry_after_seconds
        super().__init__(
            message,
            category=_provider_error_category(error_type),
            retryable=retryable,
            safe_message=_provider_safe_message(error_type),
            metadata=error_metadata,
        )
        self.message = message
        self.provider_id = provider_id
        self.status_code = status_code
        self.error_type = error_type
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


def _provider_error_category(error_type: str | None) -> ErrorCategory:
    return {
        "rate_limit_error": ErrorCategory.RATE_LIMIT,
        "context_limit_exceeded": ErrorCategory.CONTEXT_LIMIT,
        "network_error": ErrorCategory.NETWORK,
        "timeout_error": ErrorCategory.NETWORK,
        "configuration_error": ErrorCategory.CONFIGURATION,
        "invalid_response": ErrorCategory.INVALID_RESPONSE,
        "invalid_tool_arguments": ErrorCategory.INVALID_RESPONSE,
    }.get(error_type or "", ErrorCategory.PROVIDER)


def _provider_safe_message(error_type: str | None) -> str:
    return {
        "rate_limit_error": "Provider rate limit error.",
        "context_limit_exceeded": "Provider context limit exceeded.",
        "network_error": "Provider network error.",
        "timeout_error": "Provider model call timed out.",
        "configuration_error": "Provider configuration error.",
        "invalid_response": "Provider returned an invalid response.",
        "invalid_tool_arguments": "Provider returned invalid tool arguments.",
    }.get(error_type or "", "Provider error.")


@dataclass
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    # Providers may expose hidden reasoning separately from visible output.
    # Keep these optional so an adapter never invents values when the
    # provider does not return the corresponding usage detail.
    reasoning_tokens: int | None = None
    visible_output_tokens: int | None = None

    def add(self, other: "ModelUsage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens
        if other.reasoning_tokens is not None:
            self.reasoning_tokens = (self.reasoning_tokens or 0) + other.reasoning_tokens
        if other.visible_output_tokens is not None:
            self.visible_output_tokens = (
                (self.visible_output_tokens or 0) + other.visible_output_tokens
            )


@dataclass(frozen=True)
class LLMResponse:
    assistant_message: dict[str, Any]
    final_text: str
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)
    stop_reason: str | None = None
    usage: ModelUsage | None = None
    output_interrupted: bool = False
