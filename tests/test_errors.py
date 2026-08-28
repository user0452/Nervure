from __future__ import annotations

import asyncio
import errno

from services.errors import (
    AbortError,
    RetryExhaustedError,
    ErrorCategory,
    NervureError,
    OneCodeError,
    error_message,
    errno_code,
    errno_path,
    is_abort_error,
    is_fs_inaccessible,
    nervure_error_details,
    short_error_stack,
    to_error,
    actionable_error_message,
)
from services.model.types import ProviderError


def test_nervure_error_details_for_base_error() -> None:
    error = NervureError(
        "full message",
        category=ErrorCategory.TOOL,
        retryable=True,
        safe_message="safe message",
        metadata={"tool_name": "read_file"},
    )

    details = nervure_error_details(error)

    assert details.category == ErrorCategory.TOOL
    assert details.error_type == "NervureError"
    assert details.message == "full message"
    assert details.safe_message == "safe message"
    assert details.retryable is True
    assert details.metadata == {"tool_name": "read_file"}


def test_provider_error_inherits_nervure_error_with_legacy_alias() -> None:
    error = ProviderError(
        "too many requests",
        provider_id="openai",
        status_code=429,
        error_type="rate_limit_error",
        retryable=True,
        retry_after_seconds=2.5,
    )

    details = nervure_error_details(error)

    assert isinstance(error, NervureError)
    assert OneCodeError is NervureError
    assert error.provider_id == "openai"
    assert error.status_code == 429
    assert error.error_type == "rate_limit_error"
    assert error.retry_after_seconds == 2.5
    assert details.category == ErrorCategory.RATE_LIMIT
    assert details.retryable is True
    assert details.safe_message == "Provider rate limit error."


def test_filesystem_inaccessible_helpers() -> None:
    error = FileNotFoundError(errno.ENOENT, "missing", "missing.txt")

    assert is_fs_inaccessible(error) is True
    assert errno_code(error) == "ENOENT"
    assert errno_path(error) == "missing.txt"

    details = nervure_error_details(error)

    assert details.category == ErrorCategory.FILESYSTEM
    assert details.metadata["errno"] == "ENOENT"
    assert details.metadata["path"] == "missing.txt"


def test_abort_helpers() -> None:
    assert is_abort_error(AbortError()) is True
    assert is_abort_error(asyncio.CancelledError()) is True

    details = nervure_error_details(AbortError("stopped"))

    assert details.category == ErrorCategory.ABORT
    assert details.message == "stopped"


def test_error_message_and_to_error_for_unknown_values() -> None:
    error = to_error({"bad": "shape"})

    assert isinstance(error, Exception)
    assert error_message(error) == "{'bad': 'shape'}"


def test_short_error_stack_limits_traceback_frames() -> None:
    def first() -> None:
        second()

    def second() -> None:
        raise RuntimeError("boom")

    try:
        first()
    except RuntimeError as exc:
        stack = short_error_stack(exc, max_frames=1)

    assert "RuntimeError: boom" in stack
    assert "second" in stack
    assert "first" not in stack


def test_retry_exhaustion_message_has_actionable_rate_limit_next_step() -> None:
    error = RetryExhaustedError(
        "exhausted",
        metadata={"error_type": "rate_limit_error"},
    )

    message = actionable_error_message(error)

    assert "rate limit" in message
    assert "retry" in message
