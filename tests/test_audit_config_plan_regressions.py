from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from infrastructure.config.env import load_provider_config
from services.model.deadline import stream_with_wall_clock_deadline
from services.model.types import ProviderError
from services.plans.store import PlanFile


@pytest.mark.parametrize("field", ["NERVURE_TIMEOUT_SECONDS", "NERVURE_MODEL_CALL_TIMEOUT_SECONDS"])
@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "0", "-1"])
def test_provider_timeouts_must_be_finite_and_positive(tmp_path, field, value):
    env = tmp_path / ".env"
    env.write_text(
        f"NERVURE_PROVIDER_ID=custom\nCUSTOM_BASE_URL=https://example.invalid/v1\n"
        f"CUSTOM_MODEL=fake\nCUSTOM_API_KEY=fake\n{field}={value}\n",
        encoding="utf-8",
    )
    with pytest.raises(ProviderError):
        load_provider_config(env)


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), float("-inf")])
def test_deadline_rejects_nonfinite_direct_inputs(timeout):
    async def source():
        yield "done"

    async def run():
        return [event async for event in stream_with_wall_clock_deadline(source(), timeout_seconds=timeout)]

    with pytest.raises(ValueError):
        asyncio.run(run())


def test_plan_write_failure_preserves_existing_plan(tmp_path, monkeypatch):
    path = tmp_path / "plan.md"
    path.write_text("original plan", encoding="utf-8")
    original_write = Path.write_text

    def fail_mid_write(target, *args, **kwargs):
        original_write(target, "partial", encoding="utf-8")
        raise OSError("synthetic disk failure")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "write_text", fail_mid_write)
        with pytest.raises(OSError, match="synthetic"):
            PlanFile("plan", path).write("updated plan")
    assert path.read_text(encoding="utf-8") == "original plan"
    assert list(tmp_path.iterdir()) == [path]
