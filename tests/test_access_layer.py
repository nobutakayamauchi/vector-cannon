from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from vector_cannon.direct import DirectShot
from vector_cannon.ledger import load_runs, summarize_runs


class _DirectGateway:
    provider_id = "vercel"
    has_exact_credit_meter = False

    def find_model(self, model: str):
        return {"id": model, "pricing": {"input": "0.000001", "output": "0.000002"}}

    def catalog_pricing(self, model):
        return Decimal("0.000001"), Decimal("0.000002")

    def declared_free_model(self, model: str) -> bool:
        return False

    def add_request_metadata(self, request, *, tag: str) -> None:
        request["tag"] = tag

    def add_reasoning(self, request, effort) -> None:
        if effort:
            request["reasoning"] = {"effort": effort}

    @staticmethod
    def usage_tokens(payload):
        return 100, 50

    @staticmethod
    def reported_cost(payload):
        return None

    def chat_completion(self, payload):
        assert payload["model"] == "test/model"
        assert payload["messages"][1]["content"] == "fire this"
        return {
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "target hit"},
                }
            ],
        }


def test_direct_shot_fires_without_repository_and_writes_ledger(tmp_path: Path) -> None:
    result = DirectShot(gateway=_DirectGateway()).fire(
        model="test/model",
        prompt="fire this",
        max_usd=Decimal("1.00"),
        log_dir=tmp_path,
        tag="access-test",
    )
    assert result.kind == "direct-shot"
    assert result.answer == "target hit"
    assert result.observed_cost == "0.000200"
    assert result.stop_reason == "stop"
    logs = list(tmp_path.glob("*.json"))
    assert len(logs) == 1
    payload = json.loads(logs[0].read_text(encoding="utf-8"))
    assert payload["provider"] == "vercel"
    assert payload["model"] == "test/model"


def test_direct_shot_fails_closed_when_prompt_consumes_budget(tmp_path: Path) -> None:
    try:
        DirectShot(gateway=_DirectGateway()).fire(
            model="test/model",
            prompt="x" * 3000,
            max_usd=Decimal("0.0001"),
            log_dir=tmp_path,
        )
    except Exception as exc:
        assert "blocked before firing" in str(exc)
    else:
        raise AssertionError("expected budget preflight to block the shot")


def test_ledger_aggregates_direct_and_repository_style_logs(tmp_path: Path) -> None:
    records = [
        {
            "kind": "direct-shot",
            "provider": "openrouter",
            "model": "model/a",
            "observed_cost": "0.10",
            "elapsed_seconds": 2.0,
            "input_tokens": 100,
            "output_tokens": 20,
            "stop_reason": "stop",
        },
        {
            "provider": "openrouter",
            "model": "model/a",
            "observed_cost": "0.20",
            "elapsed_seconds": 4.0,
            "input_tokens": 200,
            "output_tokens": 40,
            "stop_reason": "completed",
        },
        {
            "provider": "vercel",
            "model": "model/b",
            "observed_cost": "0.50",
            "elapsed_seconds": 1.0,
            "input_tokens": 50,
            "output_tokens": 10,
            "stop_reason": "truncated",
        },
    ]
    for index, record in enumerate(records):
        (tmp_path / f"20260906-{index}.json").write_text(json.dumps(record), encoding="utf-8")

    rows = summarize_runs(load_runs(tmp_path))
    assert len(rows) == 2
    openrouter = next(row for row in rows if row.provider == "openrouter")
    assert openrouter.shots == 2
    assert openrouter.total_cost == Decimal("0.30")
    assert openrouter.average_seconds == 3.0
    assert openrouter.input_tokens == 300
    assert openrouter.output_tokens == 60
    assert openrouter.failures == 0

    vercel = next(row for row in rows if row.provider == "vercel")
    assert vercel.failures == 1
