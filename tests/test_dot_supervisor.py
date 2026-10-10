from __future__ import annotations

from pathlib import Path

import pytest

from vector_cannon.outcome.engine import OutcomeEngine
from vector_cannon.outcome.model import Contract, ControlError, canonical, digest
from vector_cannon.outcome.store import Store
from vector_cannon.outcome.supervisor import build_dot_handoff, load_handoff, submit_supervisor_decision


def contract_payload(*, supervisor="dot", shots=3):
    return {
        "schema": 1,
        "id": "dot-supervisor-test",
        "goal": "Create out.txt containing exactly ready followed by one newline.",
        "allowed_files": ["out.txt"],
        "criteria": [{
            "id": "AC-1",
            "description": "Exact expected content.",
            "kind": "text_equals",
            "path": "out.txt",
            "expect": "ready\n",
        }],
        "limits": {
            "shots": shots,
            "seconds": 300,
            "no_progress": 2,
            "judge_calls": {"jev": 3, "sol": 2, "astra": 1},
            "max_usd": None,
            "reserve_usd": {"worker": None, "jev": None, "sol": None, "astra": None},
            "unknown_cost": "bounded_calls",
        },
        "semantic_review": True,
        "human_final": False,
        "data_policy": "redacted_evidence",
        "supervisor": supervisor,
    }


class RecordingAgent:
    def __init__(self, values=("ready\n",)):
        self.values = values
        self.calls = 0
        self.directives = []

    def identity(self):
        return {"kind": "SIMULATED", "fixture": "dot-supervisor"}

    def preflight(self, workspace):
        pass

    def execute(self, contract, workspace, logs, directive, timeout, lock_fd):
        self.directives.append(dict(directive))
        value = self.values[min(self.calls, len(self.values) - 1)]
        self.calls += 1
        if value is not None:
            (workspace / "out.txt").write_text(value)
        return {
            "kind": "SIMULATED",
            "cost_usd": "0",
            "cost_status": "MEASURED",
            "worker_claim": {"status": "completed", "summary": "fixture says done"},
        }


class ChoiceJudge:
    def __init__(self, role, choice="ESCALATE", confidence=0.99):
        self.role = role
        self.choice = choice
        self.confidence = confidence
        self.calls = 0

    def identity(self):
        return {"kind": "SIMULATED_DECISION", "role": self.role, "choice": self.choice}

    def decide(self, contract, evidence, timeout):
        self.calls += 1
        return {
            "kind": "SIMULATED_DECISION",
            "role": self.role,
            "model": "fixture",
            "valid": True,
            "choice": self.choice,
            "confidence": self.confidence,
            "confidence_kind": "FIXTURE",
            "evidence_hash": evidence["hash"],
            "cost_usd": "0",
            "cost_status": "MEASURED",
        }


def make(tmp_path, *, judges=None, agent=None, supervisor="dot", shots=3):
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("baseline\n")
    agent = agent or RecordingAgent()
    contract = Contract.parse(contract_payload(supervisor=supervisor, shots=shots))
    engine = OutcomeEngine.create(contract, source, tmp_path / "session", agent, judges or {})
    return engine, agent


def decision_for(report, action, summary="Supervisor decision"):
    ref = report["handoff"]
    return {
        "schema": 1,
        "handoff_hash": ref["handoff_hash"],
        "evidence_hash": ref["evidence_hash"],
        "action": action,
        "summary": summary,
    }


@pytest.mark.parametrize("_iteration", range(3))
def test_dot_mode_stops_after_sol_without_direct_astra_call(tmp_path, _iteration):
    jev = ChoiceJudge("jev", "ESCALATE")
    sol = ChoiceJudge("sol", "ESCALATE")
    astra = ChoiceJudge("astra", "ACCEPT")
    engine, _ = make(tmp_path, judges={"jev": jev, "sol": sol, "astra": astra})

    report = engine.run()

    assert report["state"] == "DOT_HANDOFF"
    assert report["reason"] == "DOT_SUPERVISOR_REVIEW_REQUIRED"
    assert [jev.calls, sol.calls, astra.calls] == [1, 1, 0]
    assert Path(report["handoff"]["json"]).is_file()
    assert Path(report["handoff"]["markdown"]).is_file()
    payload = load_handoff(engine.store, engine.store.load())
    assert payload["identity_assurance"] == "NO_DOT_API_OR_CRYPTOGRAPHIC_IDENTITY_ATTESTATION"
    assert payload["allowed_actions"] == ["ACCEPT", "HUMAN_REQUIRED", "REPAIR"]
    assert payload["checks"][0]["status"] == "PASS"


def test_dot_mode_can_handoff_when_jev_is_not_configured(tmp_path):
    engine, _ = make(tmp_path, judges={})
    report = engine.run()
    assert report["state"] == "DOT_HANDOFF"
    assert report["reason"] == "DECISION_TIER_UNAVAILABLE:jev"
    assert report["calls"]["jev"] == 0


def test_dot_accept_is_hash_bound_then_sealed_on_resume(tmp_path):
    jev = ChoiceJudge("jev", "ESCALATE")
    sol = ChoiceJudge("sol", "ESCALATE")
    engine, agent = make(tmp_path, judges={"jev": jev, "sol": sol})
    report = engine.run()

    submitted = submit_supervisor_decision(
        engine.store,
        decision_for(report, "ACCEPT", "Hard checks pass; semantic result is acceptable."),
    )
    assert submitted["state"] == "SEAL"
    assert submitted["supervisor_decision"]["identity_assurance"] == "UNATTESTED_EXTERNAL_SUPERVISOR"

    final = OutcomeEngine(Store(engine.store.directory), agent, {"jev": jev, "sol": sol}).run()
    assert final["state"] == "VERIFIED_DONE"
    assert final["calls"]["astra"] == 0


def test_stale_dot_decision_is_rejected(tmp_path):
    engine, _ = make(tmp_path, judges={})
    report = engine.run()
    decision = decision_for(report, "ACCEPT")
    decision["evidence_hash"] = "stale"

    with pytest.raises(ControlError, match="STALE_SUPERVISOR_EVIDENCE"):
        submit_supervisor_decision(engine.store, decision)


def test_dot_repair_note_reaches_next_worker_without_scope_change(tmp_path):
    jev = ChoiceJudge("jev", "ESCALATE")
    sol = ChoiceJudge("sol", "ESCALATE")
    engine, agent = make(tmp_path, judges={"jev": jev, "sol": sol},
                         agent=RecordingAgent(("ready\n", "ready\n")))
    first = engine.run()
    submit_supervisor_decision(
        engine.store,
        decision_for(first, "REPAIR", "Re-check naming clarity only; keep the original scope."),
    )

    second = OutcomeEngine(Store(engine.store.directory), agent, {"jev": jev, "sol": sol}).run(
        max_operations=1
    )
    assert second["state"] == "VERIFY"
    assert agent.calls == 2
    assert agent.directives[1]["supervisor_note"] == "Re-check naming clarity only; keep the original scope."
    assert Contract.parse(engine.store.load()["contract"]).payload["allowed_files"] == ["out.txt"]


def test_supervisor_cannot_accept_failed_controller_checks(tmp_path):
    engine, _ = make(tmp_path, judges={}, agent=RecordingAgent(("bad\n",)), shots=1)
    report = engine.run()
    assert report["state"] == "LIMIT_REACHED"

    # Build a handoff from the current failed evidence only to exercise the hard guard.
    with engine.store.lock():
        state = engine.store.load()
        contract = Contract.parse(state["contract"])
        state["handoff"] = build_dot_handoff(
            engine.store, state, contract, reason="FIXTURE_FAILED_EVIDENCE"
        )
        state.update(phase="DOT_HANDOFF", reason="FIXTURE_FAILED_EVIDENCE")
        engine.store.save(state, "FIXTURE_DOT_HANDOFF")

    report = engine.store.report()
    with pytest.raises(ControlError, match="SUPERVISOR_CANNOT_OVERRIDE_FAILED_CHECKS"):
        submit_supervisor_decision(engine.store, decision_for(report, "ACCEPT"))


def test_human_supervisor_mode_preserves_legacy_astra_path(tmp_path):
    jev = ChoiceJudge("jev", "ESCALATE")
    sol = ChoiceJudge("sol", "ESCALATE")
    astra = ChoiceJudge("astra", "ACCEPT")
    engine, _ = make(
        tmp_path,
        judges={"jev": jev, "sol": sol, "astra": astra},
        supervisor="human",
    )

    report = engine.run()
    assert report["state"] == "VERIFIED_DONE"
    assert [jev.calls, sol.calls, astra.calls] == [1, 1, 1]
    assert report["handoff"] is None


def test_invalid_supervisor_contract_is_rejected():
    with pytest.raises(ControlError, match="INVALID_SUPERVISOR"):
        Contract.parse(contract_payload(supervisor="invented-dot-api"))


def test_omitted_supervisor_preserves_legacy_contract_hash():
    raw = contract_payload(supervisor="human")
    raw.pop("supervisor")
    parsed = Contract.parse(raw)
    assert "supervisor" not in parsed.payload
    assert parsed.hash == digest(raw)
