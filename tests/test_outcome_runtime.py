"""Offline tests for the OS process runner, not a real Codex/model acceptance test."""
from __future__ import annotations

import sys
from pathlib import Path

from vector_cannon.outcome.adapters import process
from vector_cannon.outcome.model import Contract
from vector_cannon.outcome.verify import verify


def test_process_runner_executes_bounded_local_fixture(tmp_path):
    result = process([sys.executable, "-c", "print('fixture ok')"], tmp_path, tmp_path / "logs", 2)
    assert result["exit_code"] == 0
    assert result["timed_out"] is False
    assert result["output_limited"] is False
    assert Path(result["stdout"]).read_text() == "fixture ok\n"
    assert not (tmp_path / "logs/stdin.txt").exists()


def test_process_timeout_kills_local_fixture(tmp_path):
    result = process([sys.executable, "-c", "import time; time.sleep(5)"], tmp_path, tmp_path / "logs", 0.05)
    assert result["timed_out"] is True
    assert result["exit_code"] != 0


def test_fast_oversized_output_is_flagged_even_when_process_has_exited(tmp_path):
    result = process([sys.executable, "-c", "print('x' * (3 * 1024 * 1024))"], tmp_path, tmp_path / "logs", 2)
    assert result["output_limited"] is True
    assert Path(result["stdout"]).stat().st_size <= 2 * 1024 * 1024


def test_process_does_not_inherit_gateway_credential(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "do-not-leak-this-fixture")
    result = process([sys.executable, "-c", "import os; print(os.environ.get('AI_GATEWAY_API_KEY', 'absent'))"],
                     tmp_path, tmp_path / "logs", 2)
    assert Path(result["stdout"]).read_text() == "absent\n"


def test_verifier_rejects_truncated_output_even_with_exit_zero(tmp_path):
    contract = Contract.parse({
        "schema": 1, "id": "verifier-limit", "goal": "Run the declared test", "allowed_files": ["out.txt"],
        "criteria": [{"id": "AC-1", "description": "Complete test output", "kind": "command", "path": None,
                      "expect": ["true"]}],
        "limits": {"shots": 1, "seconds": 60, "no_progress": 1,
                   "judge_calls": {"jev": 0, "sol": 0, "astra": 0}, "max_usd": None,
                   "reserve_usd": {"worker": None, "jev": None, "sol": None, "astra": None},
                   "unknown_cost": "bounded_calls"},
    })
    evidence = verify(contract, tmp_path, revision=0, command=lambda *args: {
        "exit_code": 0, "timed_out": False, "sandbox_verified": True, "output_limited": True})
    assert evidence["checks"][0]["status"] == "FAIL"
