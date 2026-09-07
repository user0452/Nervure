from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from evals.live_modules import LiveBudgetExceeded, MeteredClient, main
from services.context.snapshot import ContextSnapshot
from services.model.stream import ModelStreamEvent
from services.model.types import ModelUsage


class FakeClient:
    config = SimpleNamespace()

    def __init__(self, *, fail=False):
        self.snapshots = []
        self.fail = fail

    def _build_payload(self, snapshot):
        return {"messages": list(snapshot.messages)}

    async def stream(self, snapshot):
        self.snapshots.append(snapshot)
        if self.fail:
            raise RuntimeError("synthetic")
        usage = ModelUsage(input_tokens=30, output_tokens=10)
        yield ModelStreamEvent.usage_event(usage)
        yield ModelStreamEvent.message_completed(
            assistant_message={"role": "assistant", "content": "done"},
            final_text="done", stop_reason="stop", usage=usage,
        )


def drain(client, snapshot=None):
    async def run():
        return [event async for event in client.stream(snapshot or ContextSnapshot("", ()))]
    return asyncio.run(run())


def test_budget_rejects_before_any_network_io():
    inner = FakeClient()
    client = MeteredClient(inner, budget=1)
    with pytest.raises(LiveBudgetExceeded):
        drain(client)
    assert inner.snapshots == []


def test_cumulative_usage_events_are_not_added_twice():
    client = MeteredClient(FakeClient())
    drain(client)
    assert client.summary()["actual_total_tokens"] == 40
    assert client.summary()["calls_without_usage"] == 0


def test_failed_calls_keep_reservation_and_missing_usage():
    client = MeteredClient(FakeClient(fail=True))
    with pytest.raises(RuntimeError, match="synthetic"):
        drain(client)
    assert client.reserved > 0
    assert client.summary()["calls_without_usage"] == 1


def test_output_recovery_cannot_bypass_live_output_cap():
    inner = FakeClient()
    client = MeteredClient(inner, max_output=1024)
    original = ContextSnapshot("", (), usage_hints={"request_overrides": {"max_output_tokens": 32000}})
    drain(client, original)
    assert inner.snapshots[0].usage_hints["request_overrides"]["max_output_tokens"] == 1024
    assert original.usage_hints["request_overrides"]["max_output_tokens"] == 32000


def test_live_cli_requires_explicit_opt_in(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["--output", str(tmp_path / "unused")])
    assert exc.value.code == 2
    assert not (tmp_path / "unused").exists()
