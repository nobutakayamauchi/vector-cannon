from __future__ import annotations

import json
import math
import multiprocessing
from pathlib import Path

import pytest

from vector_cannon.outcome.adapters import ACTIONS, ImportAgent, NativeJev, TextJudge, parsed_choice, http_post
from vector_cannon.outcome.cli import outcome_main
from vector_cannon.outcome.engine import OutcomeEngine
from vector_cannon.outcome.model import Contract, ControlError, canonical, copy_snapshot, digest, manifest, strict_json
from vector_cannon.outcome.store import Store
from vector_cannon.outcome.verify import decision_input, verify


def payload(**updates):
    data = {
        "schema": 1, "id": "outcome-test", "goal": "Deliver out.txt containing ready plus one newline.",
        "allowed_files": ["out.txt"],
        "criteria": [{"id": "AC-1", "description": "The agreed content is exact.",
                      "kind": "text_equals", "path": "out.txt", "expect": "ready\n"}],
        "limits": {"shots": 3, "seconds": 300, "no_progress": 2,
                   "judge_calls": {"jev": 3, "sol": 2, "astra": 1},
                   "max_usd": None, "reserve_usd": {r: None for r in ("worker", "jev", "sol", "astra")},
                   "unknown_cost": "bounded_calls"},
        "semantic_review": False, "human_final": False, "data_policy": "local_only",
    }
    data.update(updates)
    return data


class FixtureAgent:
    """Writes test fixtures, not a stand-in advertised as a real AI connection."""
    def __init__(self, outputs=("bad\n", "ready\n"), *, cost="0", extra=None, crash=False):
        self.outputs, self.calls = outputs, 0
        self.cost, self.extra, self.crash = cost, extra, crash
        self.instructions = []

    def identity(self):
        return {"kind": "SIMULATED", "fixture": "two-shot", "assurance": "NOT_REAL_AI"}

    def preflight(self, workspace):
        pass

    def execute(self, contract, workspace, logs, directive, timeout, lock_fd):
        self.calls += 1
        self.instructions.append(directive)
        if self.crash:
            raise OSError("fixture connection lost")
        value = self.outputs[min(self.calls - 1, len(self.outputs) - 1)]
        if value is not None:
            (workspace / "out.txt").write_text(value)
        if self.extra:
            p = workspace / self.extra
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("unexpected")
        return {"kind": "SIMULATED", "worker_claim": {"status": "completed", "summary": "everything works"},
                "cost_usd": self.cost, "cost_status": "MEASURED" if self.cost is not None else "UNKNOWN"}


class FixtureJudge:
    def __init__(self, role, choices=("ACCEPT",), confidence=0.99, stale=False, invalid=False):
        self.role, self.choices, self.confidence = role, choices, confidence
        self.calls, self.stale, self.invalid = 0, stale, invalid

    def identity(self):
        return {"kind": "SIMULATED_DECISION", "role": self.role}

    def decide(self, contract, evidence, timeout):
        action = self.choices[min(self.calls, len(self.choices) - 1)]
        self.calls += 1
        return {"kind": "SIMULATED_DECISION", "role": self.role, "model": "fixture",
                "valid": not self.invalid, "choice": action, "confidence": self.confidence,
                "evidence_hash": "stale" if self.stale else evidence["hash"],
                "cost_usd": "0.001", "cost_status": "MEASURED"}


def make_engine(tmp_path, agent=None, judges=None, **updates):
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("original\n")
    agent = agent or FixtureAgent()
    engine = OutcomeEngine.create(Contract.parse(payload(**updates)), source, tmp_path / "session", agent, judges)
    return engine, source, agent


def test_scoped_failed_candidate_is_retained_then_repaired(tmp_path):
    engine, source, agent = make_engine(tmp_path)
    original = manifest(source)
    report = engine.run()
    assert report["state"] == "VERIFIED_DONE"
    assert report["calls"] == {"worker": 2, "jev": 0, "sol": 0, "astra": 0}
    assert agent.instructions[1]["checks"][0]["status"] == "FAIL"
    assert (engine.store.directory / "work-001/out.txt").read_text() == "bad\n"
    assert (Path(report["output"]["directory"]) / "out.txt").read_text() == "ready\n"
    assert "+ready" in Path(report["output"]["patch"]).read_text()
    assert manifest(source) == original
    assert report["deployment"] == "NOT_PERFORMED"
    assert report["execution_assurance"]["kind"] == "SIMULATED"


def test_completed_candidate_skips_every_judge_when_review_not_requested(tmp_path):
    judges = {r: FixtureJudge(r) for r in ("jev", "sol", "astra")}
    engine, _, _ = make_engine(tmp_path, FixtureAgent(("ready\n",)), judges)
    report = engine.run()
    assert report["state"] == "VERIFIED_DONE"
    assert all(j.calls == 0 for j in judges.values())


def test_jev_only_semantic_accept_never_calls_sol_or_astra(tmp_path):
    judges = {r: FixtureJudge(r) for r in ("jev", "sol", "astra")}
    engine, _, _ = make_engine(tmp_path, FixtureAgent(("ready\n",)), judges,
                               semantic_review=True, data_policy="redacted_evidence")
    report = engine.run()
    assert report["state"] == "VERIFIED_DONE"
    assert [judges[r].calls for r in ("jev", "sol", "astra")] == [1, 0, 0]


def test_low_confidence_reaches_astra_then_human_without_accept(tmp_path):
    judges = {r: FixtureJudge(r, confidence=0.7) for r in ("jev", "sol", "astra")}
    engine, _, _ = make_engine(tmp_path, FixtureAgent(("ready\n",)), judges,
                               semantic_review=True, data_policy="redacted_evidence")
    report = engine.run()
    assert report["state"] == "HUMAN_REQUIRED"
    assert report["reason"] == "AUTOMATED_REVIEW_EXHAUSTED"
    assert report["calls"]["astra"] == 1
    assert report["output"] is None


def test_stale_decision_is_rejected_not_done(tmp_path):
    engine, _, _ = make_engine(tmp_path, FixtureAgent(("ready\n",)), {"jev": FixtureJudge("jev", stale=True)},
                               semantic_review=True, data_policy="redacted_evidence")
    report = engine.run()
    assert report["state"] == "HUMAN_REQUIRED"
    assert report["decisions"][0]["valid"] is False


def test_expensive_judge_cap_is_persisted_across_rounds(tmp_path):
    judges = {"jev": FixtureJudge("jev", ("ESCALATE",)), "sol": FixtureJudge("sol", ("ESCALATE",)),
              "astra": FixtureJudge("astra", ("REPAIR",))}
    engine, _, _ = make_engine(tmp_path, FixtureAgent(("ready\n",)), judges,
                               semantic_review=True, data_policy="redacted_evidence")
    report = engine.run()
    assert report["state"] == "LIMIT_REACHED"
    assert report["calls"]["astra"] == 1
    assert judges["astra"].calls == 1


def test_unknown_cost_does_not_become_free_or_restart(tmp_path):
    data = payload()
    data["limits"]["unknown_cost"] = "stop"
    data["limits"]["reserve_usd"]["worker"] = "0.1"
    engine, _, agent = make_engine(tmp_path, FixtureAgent(cost=None), limits=data["limits"])
    report = engine.run()
    assert report["state"] == "BLOCKED"
    assert report["reason"] == "UNRECONCILED_COST"
    assert agent.calls == 1
    assert report["unknown_cost_calls"] == 1
    assert report["cost_per_verified_outcome_usd"] is None


def test_monetary_cap_requires_reservation_before_call(tmp_path):
    data = payload()
    data["limits"]["max_usd"] = "0.1"
    engine, _, agent = make_engine(tmp_path, limits=data["limits"])
    report = engine.run()
    assert report["reason"] == "COST_RESERVATION_REQUIRED"
    assert agent.calls == 0


def test_actual_overspend_stops_before_followup(tmp_path):
    data = payload()
    data["limits"]["max_usd"] = "0.1"
    data["limits"]["reserve_usd"]["worker"] = "0.05"
    engine, _, agent = make_engine(tmp_path, FixtureAgent(cost="0.2"), limits=data["limits"])
    report = engine.run()
    assert report["state"] == "LIMIT_REACHED"
    assert report["known_cost_usd"] == "0.2"
    assert agent.calls == 1


def test_resume_checkpoint_does_not_replay_completed_worker(tmp_path):
    engine, _, agent = make_engine(tmp_path, FixtureAgent(("ready\n",)))
    paused = engine.run(max_operations=1)
    assert paused["state"] == "VERIFY"
    resumed = OutcomeEngine(Store(engine.store.directory), agent).run()
    assert resumed["state"] == "VERIFIED_DONE"
    assert agent.calls == 1
    again = OutcomeEngine(Store(engine.store.directory), agent).run()
    assert again["state"] == "VERIFIED_DONE" and agent.calls == 1


def test_uncertain_external_request_not_replayed_on_resume(tmp_path):
    engine, _, agent = make_engine(tmp_path, FixtureAgent(crash=True))
    report = engine.run()
    assert report["state"] == "HUMAN_REQUIRED"
    assert report["in_flight"] == "worker-001"
    result = OutcomeEngine(Store(engine.store.directory), agent).run()
    assert result["reason"] == "UNCERTAIN_EXTERNAL_OPERATION_DO_NOT_REPLAY"
    assert agent.calls == 1


def test_human_acceptance_bound_to_evidence_hash(tmp_path):
    engine, _, _ = make_engine(tmp_path, FixtureAgent(("ready\n",)), human_final=True)
    report = engine.run()
    assert report["state"] == "HUMAN_REQUIRED"
    with pytest.raises(ControlError, match="ACCEPTANCE_NOT_APPLICABLE"):
        engine.accept("invented")
    result = engine.accept(report["evidence_hash"])
    assert result["state"] == "VERIFIED_DONE"


def test_human_cannot_accept_failed_output(tmp_path):
    data = payload()
    data["limits"]["shots"] = 1
    engine, _, _ = make_engine(tmp_path, FixtureAgent(("bad\n",)), human_final=True, limits=data["limits"])
    report = engine.run()
    with pytest.raises(ControlError, match="ACCEPTANCE_NOT_APPLICABLE"):
        engine.accept(report["evidence_hash"])


def test_missing_external_data_permission_prevents_api_call(tmp_path):
    judge = FixtureJudge("jev")
    engine, _, _ = make_engine(tmp_path, FixtureAgent(("ready\n",)), {"jev": judge}, semantic_review=True)
    report = engine.run()
    assert report["reason"] == "EXTERNAL_EVIDENCE_NOT_AUTHORIZED"
    assert judge.calls == 0


@pytest.mark.parametrize("extra", ["unauthorized.txt", "node_modules/hidden.js", "__pycache__/hidden.pyc"])
def test_scope_or_hidden_directory_changes_block_done(tmp_path, extra):
    engine, _, _ = make_engine(tmp_path, FixtureAgent(("ready\n",), extra=extra))
    report = engine.run()
    assert report["state"] in {"BLOCKED", "HUMAN_REQUIRED"}
    assert report["output"] is None


def test_changed_candidate_rejected_on_resume(tmp_path):
    engine, _, agent = make_engine(tmp_path)
    engine.run(max_operations=1)
    (engine.store.directory / "work-001/out.txt").write_text("tampered")
    report = OutcomeEngine(Store(engine.store.directory), agent).run()
    assert report["reason"] == "SNAPSHOT_CHANGED_OUTSIDE_CONTROLLER"
    assert agent.calls == 1


def test_changed_source_rejected_on_resume(tmp_path):
    engine, source, agent = make_engine(tmp_path)
    engine.run(max_operations=1)
    (source / "README.md").write_text("other developer changed source")
    report = OutcomeEngine(Store(engine.store.directory), agent).run()
    assert report["reason"] == "SOURCE_IDENTITY_CHANGED"
    assert agent.calls == 1


def test_session_lock_rejects_second_controller(tmp_path):
    engine, _, agent = make_engine(tmp_path)
    with engine.store.lock():
        with pytest.raises(ControlError, match="SESSION_BUSY"):
            OutcomeEngine(Store(engine.store.directory), agent).run()
    assert agent.calls == 0


def test_no_progress_stops_without_spending_review_models(tmp_path):
    engine, _, agent = make_engine(tmp_path, FixtureAgent((None,)))
    report = engine.run()
    assert report["reason"] == "NO_PROGRESS"
    assert agent.calls == 2
    assert report["calls"]["jev"] == 0


def test_unknown_command_verifier_is_not_success(tmp_path):
    criteria = [{"id": "TEST", "description": "Run tests", "kind": "command", "path": None,
                 "expect": ["python3", "-m", "pytest", "-q"]}]
    engine, _, agent = make_engine(tmp_path, criteria=criteria)
    report = engine.run()
    assert report["reason"] == "VERIFICATION_UNKNOWN"
    assert agent.calls == 0


def test_bool_exit_code_is_not_zero(tmp_path):
    c = Contract.parse(payload(criteria=[{"id": "T", "description": "Tests", "kind": "command", "path": None,
                                         "expect": ["true"]}]))
    data = verify(c, tmp_path, revision=0, command=lambda *args: {"exit_code": False, "timed_out": False, "sandbox_verified": True})
    assert data["checks"][0]["status"] == "FAIL"


def test_csv_import_real_noncode_artifacts_verified(tmp_path):
    source, candidate = tmp_path / "source", tmp_path / "candidate"
    source.mkdir()
    candidate.mkdir()
    (candidate / "leads.csv").write_text("company,url\nA,https://example.org/a\nB,https://example.org/b\n")
    c = Contract.parse(payload(allowed_files=["leads.csv"], criteria=[{
        "id": "CSV", "description": "Two unique companies with populated URL columns (not URL liveness).",
        "kind": "csv", "path": "leads.csv", "expect": {"columns": ["company", "url"], "min_rows": 2, "unique": ["url"]}}]))
    engine = OutcomeEngine.create(c, source, tmp_path / "session", ImportAgent(candidate))
    report = engine.run()
    assert report["state"] == "VERIFIED_DONE"
    assert report["execution_assurance"]["kind"] == "ARTIFACT_IMPORT"
    assert report["calls"]["jev"] == 0
    assert not list(source.iterdir())


def evidence_for_api(tmp_path):
    (tmp_path / "out.txt").write_text("ready\n")
    contract = Contract.parse(payload(semantic_review=True, data_policy="redacted_evidence"))
    return contract, verify(contract, tmp_path, revision=1)


def native_response(choice="ACCEPT", cost="0.001"):
    return {"model": "typesafe-ai/jev", "answers": {"next": {
        "type": "choice", "choice": choice, "probabilities": {k: 1 if k == choice else 0 for k in ACTIONS}}},
        "providerMetadata": {"gateway": {"cost": cost}}}


def test_native_jev_uses_decision_endpoint_not_chat(tmp_path):
    calls = []
    def transport(endpoint, body, timeout):
        calls.append((endpoint, body))
        return native_response()
    contract, evidence = evidence_for_api(tmp_path)
    result = NativeJev(transport).decide(contract, evidence, 5)
    assert calls[0][0].endswith("/v1/evaluate")
    assert "messages" not in calls[0][1]
    assert calls[0][1]["questions"]["next"]["type"] == "choice"
    assert result["valid"] is True
    assert result["evidence_hash"] == evidence["hash"]
    assert result["cost_usd"] == "0.001"


@pytest.mark.parametrize("mutation", ["nan", "bool", "missing", "model", "type", "choice", "sum"])
def test_native_jev_rejects_malformed_or_mismatched_results(tmp_path, mutation):
    reply = native_response()
    answer = reply["answers"]["next"]
    if mutation == "nan": answer["probabilities"]["ACCEPT"] = float("nan")
    if mutation == "bool": answer["probabilities"]["ACCEPT"] = True
    if mutation == "missing": del answer["probabilities"]["REPAIR"]
    if mutation == "model": reply["model"] = "not-jev"
    if mutation == "type": answer["type"] = "text"
    if mutation == "choice": answer["choice"] = "DROP_DATABASE"
    if mutation == "sum": answer["probabilities"]["ACCEPT"] = 0.1
    c, e = evidence_for_api(tmp_path)
    result = NativeJev(lambda *args: reply).decide(c, e, 5)
    assert result["valid"] is False
    assert result["cost_usd"] == "0.001"  # Invalid answers still cost money.


def test_provider_endpoint_redirect_cannot_receive_credentials():
    with pytest.raises(ControlError, match="ENDPOINT_NOT_ALLOWED"):
        http_post("https://evil.invalid/collect", {}, 1)


def test_text_roles_require_explicit_model_and_do_not_pretend_to_be_jev():
    with pytest.raises(ControlError): TextJudge("jev", "any")
    with pytest.raises(ControlError): TextJudge("astra", "")
    assert TextJudge("sol", "operator-selected/model").identity()["kind"] == "TEXT_JUDGE"


def test_strict_json_duplicate_keys_nonfinite():
    with pytest.raises(ControlError): strict_json('{"x": 1, "x": 2}')
    with pytest.raises(ControlError): strict_json('{"x": NaN}')


@pytest.mark.parametrize("bad", [True, 1.1, 0, -1, 21, "2"])
def test_invalid_shot_limits_rejected(bad):
    data = payload()
    data["limits"]["shots"] = bad
    with pytest.raises(ControlError): Contract.parse(data)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, 0, -1])
def test_invalid_deadlines_rejected(bad):
    data = payload()
    data["limits"]["seconds"] = bad
    with pytest.raises(ValueError): Contract.parse(data)


@pytest.mark.parametrize("path", ["../escape", "/tmp/out", ".env", ".git/config", "node_modules/a", "a\\b", "a/*", "a\n"])
def test_unsafe_allowed_paths_rejected(path):
    with pytest.raises(ControlError): Contract.parse(payload(allowed_files=[path]))


def test_symbolic_link_refused_during_snapshot(tmp_path):
    (tmp_path / "link").symlink_to("/etc/passwd")
    with pytest.raises(ControlError, match="SYMLINK"):
        manifest(tmp_path)


def test_cli_plan_no_ai_no_writes(tmp_path, capsys):
    path = tmp_path / "mission.json"
    path.write_text(canonical(payload()))
    before = list(tmp_path.iterdir())
    code = outcome_main(["plan", "--contract", str(path)])
    result = json.loads(capsys.readouterr().out)
    assert code == 0 and result["state"] == "PLAN_ONLY"
    assert result["model_calls"] == 0 and result["writes"] == 0
    assert list(tmp_path.iterdir()) == before


def test_delivery_modified_after_seal_is_not_reported_verified_done(tmp_path):
    engine, _, _ = make_engine(tmp_path, FixtureAgent(("ready\n",)))
    report = engine.run()
    (Path(report["output"]["directory"]) / "out.txt").write_text("corrupted")
    checked = engine.store.report()
    assert checked["state"] == "BLOCKED"
    assert checked["reason"] == "DELIVERY_CHANGED_SINCE_SEAL"
    assert checked["delivery_integrity"] == "FAILED"


def test_arbitrary_worker_status_cannot_replace_evidence(tmp_path):
    engine, _, _ = make_engine(tmp_path, FixtureAgent(("bad\n",)))
    report = engine.run()
    assert report["state"] != "VERIFIED_DONE"
    assert report["criteria_passed"] == 0


def test_mission_deadline_rechecked_on_resume(tmp_path):
    engine, _, agent = make_engine(tmp_path)
    state = engine.store.load()
    with engine.store.lock():
        state["deadline"] = 0
        engine.store.save(state, "FIXTURE_EXPIRED")
    report = engine.run()
    assert report["reason"] == "MISSION_DEADLINE"
    assert agent.calls == 0


def test_replacing_saved_contract_without_matching_event_is_detected(tmp_path):
    engine, _, _ = make_engine(tmp_path)
    with engine.store.connect() as db:
        raw = json.loads(db.execute("SELECT body FROM state").fetchone()[0])
        raw["contract"]["allowed_files"].append("unauthorized.txt")
        db.execute("UPDATE state SET body=?", (canonical(raw),))
    with pytest.raises(ControlError, match="STATE_IDENTITY_MISMATCH"):
        engine.store.load()


def test_verifier_mutation_invalidates_its_own_success(tmp_path):
    c = Contract.parse(payload(criteria=[{"id": "T", "description": "Test", "kind": "command", "path": None,
                                         "expect": ["true"]}]))
    (tmp_path / "out.txt").write_text("before")
    def command(*args):
        (tmp_path / "out.txt").write_text("forged")
        return {"sandbox_verified": True, "exit_code": 0, "timed_out": False}
    data = verify(c, tmp_path, revision=0, command=command)
    assert data["checks"][0]["status"] == "UNKNOWN"
    assert data["checks"][0]["detail"] == "VERIFIER_MUTATED_ARTIFACTS"


def test_json_bool_does_not_satisfy_expected_number(tmp_path):
    c = Contract.parse(payload(criteria=[{"id": "J", "description": "Numeric value", "kind": "json_fields", "path": "data.json",
                                         "expect": {"count": 1}}]))
    (tmp_path / "data.json").write_text('{"count": true}')
    assert verify(c, tmp_path, revision=0)["checks"][0]["status"] == "FAIL"


def test_empty_acceptance_set_rejected():
    with pytest.raises(ControlError):
        Contract.parse(payload(criteria=[]))


def test_wrong_kind_with_unhashable_value_rejected():
    data = payload()
    data["criteria"][0]["kind"] = []
    with pytest.raises(ControlError):
        Contract.parse(data)
