from __future__ import annotations

import asyncio

from infrastructure.config.env import load_provider_config
from infrastructure.providers.chat_completions import OpenAICompatibleChatCompletionsClient
from infrastructure.providers.model_catalog import ModelCatalogClient, test_model_connection as check_connection
from services.context.snapshot import ContextSnapshot


def config(tmp_path):
    path = tmp_path / ".env"
    path.write_text("NERVURE_PROVIDER_ID=ollama\nOLLAMA_MODEL=fixture\n", encoding="utf-8")
    return load_provider_config(path)


def test_ollama_runtime_uses_compatible_endpoint_without_a_key(tmp_path):
    calls = []

    class Transport:
        async def stream_json_lines(self, url, headers, payload, timeout):
            calls.append((url, headers))
            yield {"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]}

    client = OpenAICompatibleChatCompletionsClient(config(tmp_path), async_transport=Transport())

    async def run():
        return [event async for event in client.stream(ContextSnapshot("", ()))]

    assert asyncio.run(run())[-1].final_text == "done"
    assert calls == [("http://localhost:11434/v1/chat/completions", {})]


def test_ollama_runtime_model_catalog_parses_native_tags_without_a_key(tmp_path):
    class Transport:
        def get_json(self, url, headers, timeout):
            assert url == "http://localhost:11434/api/tags"
            assert headers == {}
            return {"models": [{"name": "fixture", "model": "fixture"}]}

    assert [m.id for m in ModelCatalogClient(config(tmp_path), transport=Transport()).list_models()] == ["fixture"]


def test_ollama_connection_probe_uses_same_bounded_protocol_as_runtime(tmp_path):
    calls = []

    class Transport:
        def post_json(self, url, headers, payload, timeout):
            calls.append((url, payload))
            return {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]}

    cfg = config(tmp_path)
    assert check_connection(cfg.provider, "", cfg.model, transport=Transport()) is None
    assert calls[0][0] == "http://localhost:11434/v1/chat/completions"
    assert calls[0][1]["max_tokens"] == 1
