from __future__ import annotations

from infrastructure.config.env import ResolvedProviderConfig
from infrastructure.providers.catalog import ProviderDefinition
from infrastructure.providers.chat_completions import (
    OpenAICompatibleChatCompletionsClient,
    _structured_output_response_format,
)
from services.context.snapshot import ContextSnapshot


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "selected_memories": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 5,
            }
        },
        "required": ["selected_memories"],
        "additionalProperties": False,
    }


def test_structured_output_hint_projects_to_openai_json_schema() -> None:
    schema = _schema()

    assert _structured_output_response_format(
        {
            "name": "long_term_memory_selection",
            "strict": True,
            "schema": schema,
        }
    ) == {
        "type": "json_schema",
        "json_schema": {
            "name": "long_term_memory_selection",
            "strict": True,
            "schema": schema,
        },
    }


def test_structured_output_hint_rejects_invalid_shape() -> None:
    assert _structured_output_response_format({"name": "missing-schema"}) is None
    assert _structured_output_response_format({"schema": {"type": "object"}}) is None


def test_chat_completions_payload_contains_structured_output_response_format() -> None:
    provider = ProviderDefinition(
        id="test",
        display_name="Test",
        base_url="https://example.invalid/v1",
    )
    config = ResolvedProviderConfig(
        provider=provider,
        provider_id="test",
        display_name="Test",
        base_url=provider.base_url,
        model="test-model",
        api_key="",
    )
    client = OpenAICompatibleChatCompletionsClient(config)
    schema = _schema()
    snapshot = ContextSnapshot(
        system_prompt="",
        messages=(),
        usage_hints={
            "structured_output": {
                "name": "long_term_memory_selection",
                "strict": True,
                "schema": schema,
            }
        },
    )

    payload = client._build_payload(snapshot)

    assert payload["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "long_term_memory_selection",
            "strict": True,
            "schema": schema,
        },
    }
