"""Offline tests for the OS process runner, not a real Codex/model acceptance test."""
from __future__ import annotations

import sys
from pathlib import Path

from vector_cannon.outcome import adapters as adapters_module
from vector_cannon.outcome.adapters import CodexAgent, process
from vector_cannon.outcome.engine import OutcomeEngine
from vector_cannon.outcome.model import canonical
from test_dot_supervisor import contract_payload

import pytest
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


@pytest.mark.parametrize("header", [
    "Authorization: Basic ZHVtbXk6ZHVtbXk=",
    "Proxy-Authorization: Negotiate FIXTUREtoken987",
    "Authorization=Basic FIXTUREassignment123",
])
def test_codex_worker_blocked_json_survives_header_redaction(tmp_path, monkeypatch, header):
    """Do not lose blocked status when a JSON string contains a sensitive header."""
    contract_data = contract_payload()
    contract_data["semantic_review"] = False
    contract_data["data_policy"] = "local_only"
    contract = Contract.parse(contract_data)
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("fixture\n")
    agent = object.__new__(CodexAgent)
    agent.executable = "/bin/true"
    agent.model = None
    monkeypatch.setattr(agent, "identity", lambda: {"kind": "SYNTHETIC_CODEX_ADAPTER"})
    monkeypatch.setattr(agent, "preflight", lambda _workspace: None)

    def fixture_process(argv, workspace, logs, timeout, *, text="", lock_fd=None):
        # Never launch Codex, AI gateways, or any real external process.
        (workspace / "out.txt").write_text("ready\n")
        response = logs.parent / (logs.name + ".worker.json")
        response.write_text(canonical({
            "task_id": "worker-001", "status": "blocked",
            "summary": "Fixture worker cannot continue. " + header,
        }))
        return {"exit_code": 0, "timed_out": False, "output_limited": False}

    monkeypatch.setattr(adapters_module, "process", fixture_process)
    engine = OutcomeEngine.create(contract, source, tmp_path / "session", agent)
    report = engine.run()
    assert report["state"] == "BLOCKED", report
    assert report["reason"] == "WORKER_BLOCKED_OR_EXECUTION_FAILED", report
    claim = engine.store.load()["last_receipt"]["worker_claim"]
    assert claim["status"] == "blocked"
    assert "[REDACTED]" in claim["summary"]
    assert "ZHVtbXk6ZHVtbXk=" not in claim["summary"]
    assert "FIXTUREtoken987" not in claim["summary"]
    assert "FIXTUREassignment123" not in claim["summary"]
