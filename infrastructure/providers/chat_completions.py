"""OpenAI Chat Completions compatible model client."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from infrastructure.config.env import ResolvedProviderConfig
from infrastructure.providers.http import (
    AsyncHttpTransport,
    HttpxAsyncHttpTransport,
)
from services.context.snapshot import ContextSnapshot
from services.model.deadline import stream_with_wall_clock_deadline
from services.model.stream import ModelStreamEvent
from services.model.types import ModelUsage, ProviderError
from services.tools.types import ToolCall


@dataclass
class _ToolCallAccumulator:
    index: int
    call_id: str | None = None
    name: str = ""
    arguments: str = ""
    completed: bool = False


class OpenAICompatibleChatCompletionsClient:
    def __init__(
        self,
        config: ResolvedProviderConfig,
        *,
        async_transport: AsyncHttpTransport | None = None,
    ) -> None:
        self.config = config
        self.async_transport = async_transport or HttpxAsyncHttpTransport(
            provider_id=config.provider_id
        )

    async def stream(
        self,
        snapshot: ContextSnapshot,
    ) -> AsyncIterator[ModelStreamEvent]:
        async for event in stream_with_wall_clock_deadline(
            self._stream_without_deadline(snapshot),
            timeout_seconds=self.config.model_call_timeout_seconds,
            provider_id=self.config.provider_id,
        ):
            yield event

    async def _stream_without_deadline(
        self,
        snapshot: ContextSnapshot,
    ) -> AsyncIterator[ModelStreamEvent]:
        if not self.config.model:
            raise self._configuration_error("A model must be configured before calling chat completions.")
        payload = {**self._build_payload(snapshot), "stream": True}
        final_text_parts: list[str] = []
        reasoning_text_parts: list[str] = []
        tool_accumulators: dict[int, _ToolCallAccumulator] = {}
        stop_reason: str | None = None
        usage: ModelUsage | None = None
        emitted_completed_tool_ids: set[str] = set()

        async for chunk in self.async_transport.stream_json_lines(
            _join_url(self.config.base_url, self.config.chat_completions_path),
            self._headers(),
            payload,
            self.config.timeout_seconds,
        ):
            chunk_usage = _parse_usage(chunk.get("usage"))
            if chunk_usage is not None:
                usage = chunk_usage
                yield ModelStreamEvent.usage_event(chunk_usage)

            choices = chunk.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0]
            if not isinstance(choice, dict):
                raise self._invalid_response("Provider stream choice must be an object.")
            finish_reason = _string_or_none(choice.get("finish_reason"))
            if finish_reason is not None:
                stop_reason = finish_reason
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue

            content = delta.get("content")
            if isinstance(content, str) and content:
                final_text_parts.append(content)
                yield ModelStreamEvent.content_delta(content)

            reasoning_text = _reasoning_text_from_delta(delta)
            if reasoning_text:
                reasoning_text_parts.append(reasoning_text)
                yield ModelStreamEvent.reasoning_delta(reasoning_text)

            raw_tool_calls = delta.get("tool_calls")
            if isinstance(raw_tool_calls, list):
                for raw_delta in raw_tool_calls:
                    if not isinstance(raw_delta, dict):
                        continue
                    accumulator = _update_tool_accumulator(
                        tool_accumulators,
                        raw_delta,
                    )
                    yield ModelStreamEvent.tool_call_delta(
                        metadata={
                            "index": accumulator.index,
                            "id": accumulator.call_id,
                            "name": accumulator.name,
                            "arguments_delta_chars": _arguments_delta_chars(raw_delta),
                        }
                    )

        tool_calls = self._completed_tool_calls(tool_accumulators)
        for tool_call in tool_calls:
            if tool_call.id in emitted_completed_tool_ids:
                continue
            emitted_completed_tool_ids.add(tool_call.id)
            yield ModelStreamEvent.tool_call_completed(tool_call)
        final_text = "".join(final_text_parts)
        assistant_message = _assistant_message_from_stream(final_text, tool_accumulators)
        yield ModelStreamEvent.message_completed(
            assistant_message=assistant_message,
            final_text=final_text,
            reasoning_text="".join(reasoning_text_parts),
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=usage,
            output_interrupted=_is_output_interrupted_stop_reason(stop_reason),
        )

    def _build_payload(self, snapshot: ContextSnapshot) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        if snapshot.system_prompt:
            messages.append({"role": "system", "content": snapshot.system_prompt})
        messages.extend(_project_messages(snapshot.messages))

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            **self.config.default_params,
        }
        if snapshot.tool_schemas:
            payload["tools"] = list(snapshot.tool_schemas)
        request_overrides = snapshot.usage_hints.get("request_overrides")
        if isinstance(request_overrides, dict):
            max_output_tokens = request_overrides.get("max_output_tokens")
            if isinstance(max_output_tokens, int) and max_output_tokens > 0:
                payload["max_tokens"] = max_output_tokens
        structured_output = snapshot.usage_hints.get("structured_output")
        if isinstance(structured_output, dict):
            response_format = _structured_output_response_format(structured_output)
            if response_format is not None:
                payload["response_format"] = response_format
        return payload

    def _headers(self) -> dict[str, str]:
        if not self.config.api_key:
            raise self._configuration_error("An API key must be configured before calling the provider.")
        return {
            **self.config.headers,
            "Authorization": f"Bearer {self.config.api_key}",
        }

    def _completed_tool_calls(
        self,
        accumulators: dict[int, _ToolCallAccumulator],
    ) -> tuple[ToolCall, ...]:
        parsed: list[ToolCall] = []
        for index in sorted(accumulators):
            accumulator = accumulators[index]
            if not accumulator.name:
                continue
            parsed.append(
                ToolCall(
                    id=accumulator.call_id or f"call_{index}",
                    name=accumulator.name,
                    input=self._parse_arguments(accumulator.arguments),
                )
            )
        return tuple(parsed)

    def _parse_tool_calls(self, raw_tool_calls: Any) -> tuple[ToolCall, ...]:
        if raw_tool_calls is None:
            return ()
        if not isinstance(raw_tool_calls, list):
            raise self._invalid_response("Provider tool_calls field must be a list.")

        parsed: list[ToolCall] = []
        for index, raw_tool_call in enumerate(raw_tool_calls):
            if not isinstance(raw_tool_call, dict):
                raise self._invalid_response("Provider tool call must be an object.")
            function = raw_tool_call.get("function")
            if not isinstance(function, dict):
                raise self._invalid_response("Provider tool call is missing function.")
            name = function.get("name")
            if not isinstance(name, str) or not name:
                raise self._invalid_response("Provider tool call is missing function name.")
            parsed.append(
                ToolCall(
                    id=_string_or_none(raw_tool_call.get("id")) or f"call_{index}",
                    name=name,
                    input=self._parse_arguments(function.get("arguments")),
                )
            )
        return tuple(parsed)

    def _parse_arguments(self, arguments: Any) -> dict[str, Any]:
        if arguments in (None, ""):
            return {}
        if isinstance(arguments, dict):
            return arguments
        if not isinstance(arguments, str):
            raise self._invalid_tool_arguments("Tool arguments must be a JSON object string.")
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise self._invalid_tool_arguments("Tool arguments are not valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise self._invalid_tool_arguments("Tool arguments JSON must be an object.")
        return parsed

    def _configuration_error(self, message: str) -> ProviderError:
        return ProviderError(
            message,
            provider_id=self.config.provider_id,
            error_type="configuration_error",
        )

    def _invalid_response(self, message: str) -> ProviderError:
        return ProviderError(
            message,
            provider_id=self.config.provider_id,
            error_type="invalid_response",
        )

    def _invalid_tool_arguments(self, message: str) -> ProviderError:
        return ProviderError(
            message,
            provider_id=self.config.provider_id,
            error_type="invalid_tool_arguments",
        )


def _assistant_message(
    message: dict[str, Any],
    raw_tool_calls: Any,
) -> dict[str, Any]:
    assistant_message: dict[str, Any] = {
        "role": "assistant",
        "content": message.get("content", ""),
    }
    if raw_tool_calls:
        assistant_message["tool_calls"] = raw_tool_calls
    return assistant_message


def _assistant_message_from_stream(
    final_text: str,
    accumulators: dict[int, _ToolCallAccumulator],
) -> dict[str, Any]:
    raw_tool_calls = [
        {
            "id": accumulator.call_id or f"call_{index}",
            "type": "function",
            "function": {
                "name": accumulator.name,
                "arguments": accumulator.arguments,
            },
        }
        for index, accumulator in sorted(accumulators.items())
        if accumulator.name
    ]
    return _assistant_message(
        {"content": final_text},
        raw_tool_calls if raw_tool_calls else None,
    )


def _update_tool_accumulator(
    accumulators: dict[int, _ToolCallAccumulator],
    raw_delta: dict[str, Any],
) -> _ToolCallAccumulator:
    raw_index = raw_delta.get("index")
    index = raw_index if isinstance(raw_index, int) else len(accumulators)
    accumulator = accumulators.setdefault(
        index,
        _ToolCallAccumulator(index=index),
    )
    call_id = _string_or_none(raw_delta.get("id"))
    if call_id:
        accumulator.call_id = call_id
    function = raw_delta.get("function")
    if isinstance(function, dict):
        name = _string_or_none(function.get("name"))
        if name:
            accumulator.name += name
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            accumulator.arguments += arguments
    return accumulator


def _arguments_delta_chars(raw_delta: dict[str, Any]) -> int:
    function = raw_delta.get("function")
    if not isinstance(function, dict):
        return 0
    arguments = function.get("arguments")
    return len(arguments) if isinstance(arguments, str) else 0


def _reasoning_text_from_delta(delta: dict[str, Any]) -> str:
    """Read only provider-visible reasoning fields exposed by the stream."""

    for key in ("reasoning_content", "reasoning", "thinking"):
        value = delta.get(key)
        if isinstance(value, str):
            return value
    return ""


def _structured_output_response_format(value: dict[str, Any]) -> dict[str, Any] | None:
    name = value.get("name")
    schema = value.get("schema")
    if not isinstance(name, str) or not name or not isinstance(schema, dict):
        return None
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": value.get("strict") is not False,
            "schema": schema,
        },
    }


def _project_messages(messages: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "tool_result":
            # Nervure 内部存 provider-neutral 的 tool_result；
            # Chat Completions wire format 需要 role="tool"。
            projected.append(
                {
                    "role": "tool",
                    "tool_call_id": message.get("tool_call_id", ""),
                    "content": message.get("content", ""),
                }
            )
        else:
            projected.append(dict(message))
    return projected


def _parse_usage(usage: Any) -> ModelUsage | None:
    if usage is None:
        return None
    if not isinstance(usage, dict):
        return None
    prompt_details = usage.get("prompt_tokens_details")
    if not isinstance(prompt_details, dict):
        prompt_details = {}
    completion_details = usage.get("completion_tokens_details")
    if not isinstance(completion_details, dict):
        completion_details = {}
    output_details = usage.get("output_tokens_details")
    if not isinstance(output_details, dict):
        output_details = {}
    output_tokens = _int_or_zero(usage.get("completion_tokens"))
    reasoning_tokens = _first_int(
        completion_details.get("reasoning_tokens"),
        output_details.get("reasoning_tokens"),
        usage.get("reasoning_tokens"),
    )
    visible_output_tokens = _first_int(
        completion_details.get("visible_output_tokens"),
        output_details.get("visible_output_tokens"),
        usage.get("visible_output_tokens"),
    )
    # OpenAI-compatible Chat Completions report completion_tokens as the
    # total completion budget. When reasoning_tokens is explicitly returned,
    # the remainder is the only safe visible-output derivation available.
    if visible_output_tokens is None and reasoning_tokens is not None:
        visible_output_tokens = max(0, output_tokens - reasoning_tokens)
    return ModelUsage(
        input_tokens=_int_or_zero(usage.get("prompt_tokens")),
        output_tokens=output_tokens,
        cache_read_input_tokens=_int_or_zero(prompt_details.get("cached_tokens")),
        reasoning_tokens=reasoning_tokens,
        visible_output_tokens=visible_output_tokens,
    )


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _int_or_zero(value: Any) -> int:
    return value if isinstance(value, int) else 0


def _first_int(*values: Any) -> int | None:
    for value in values:
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _is_output_interrupted_stop_reason(stop_reason: str | None) -> bool:
    return stop_reason in {"length", "max_tokens", "max_output_tokens"}
