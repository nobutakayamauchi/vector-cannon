from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.error

import pytest

from w12_edge_miner.s01.frozen import verify_frozen
from w12_edge_miner.s01.models import (
    MANDATORY, Capability, CapabilityAudit, CausalWitness, Evidence,
    State, TimingObservation, available_as_of, load_audit,
)
from w12_edge_miner.s01.probe import ENDPOINTS, NoRedirect, public_probe
from w12_edge_miner.s01.runner import encoded, run

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "reports/w12_edge_miner/s01/evidence.json"
PAST = "2026-09-18T00:00:00+00:00"
LATER = "2026-09-23T00:00:00+00:00"


def observation(**changes):
    values = dict(event_id="synthetic-event", event_time=PAST, observed_at=PAST,
                  finality_observed_at=PAST, prerequisites=())
    return TimingObservation(**(values | changes))


def claimed_complete():
    evidence = Evidence("test", "CAUSAL_CAPTURE", "Synthetic test fixture", "fixture://test",
                        LATER, "Test only, not real wallet evidence", "a" * 64, fixture=True)
    return CapabilityAudit("fixture-source", "fixture-product", {},
                           tuple(Capability(n, State.VERIFIED, "test", ("test",)) for n in MANDATORY),
                           (evidence,), (CausalWitness(observation(), "test", PAST),))


def test_historical_fetch_does_not_backdate_receipt_or_trust_source_availability():
    row = TimingObservation.received(
        {"event_id": "synthetic-old-event", "event_time": PAST,
         "observed_at": PAST, "available_at": PAST, "finality_observed_at": PAST},
        received_at=LATER,
    )
    assert row.observed_at == LATER
    assert row.finality_observed_at is None and row.available_at is None
    row = replace(row, finality_observed_at=LATER)
    assert row.available_at == LATER
    assert available_as_of((row,), PAST) == ()


@pytest.mark.parametrize("field", ("event_id", "event_time", "observed_at", "finality_observed_at"))
def test_missing_timing_or_identity_is_unknown(field):
    row = observation(**{field: None})
    assert getattr(row, field) is None
    assert row.available_at is None
    assert available_as_of((row,), LATER) == ()


def test_dependency_finality_and_local_receive_all_bound_availability():
    assert observation(prerequisites=(None,)).available_at is None
    assert observation(prerequisites=(LATER,)).available_at == LATER
    assert observation(finality_observed_at=LATER).available_at == LATER
    assert observation(event_time=LATER).available_at is None
    with pytest.raises(ValueError, match="timezone"):
        observation(event_time="2026-09-18T00:00:00")


def test_future_rows_and_revisions_cannot_change_past_availability():
    past = observation()
    missing = observation(event_id="not-yet-known", finality_observed_at=None)
    later_revision = replace(past, observed_at=LATER, finality_observed_at=None)
    later_completion = replace(missing, observed_at=LATER, finality_observed_at=LATER)
    baseline = available_as_of((past, missing), PAST)
    assert baseline == ("synthetic-event",)
    for rows in ((past, missing, later_revision, later_completion),
                 (later_completion, later_revision, missing, past)):
        assert available_as_of(rows, PAST) == baseline


@pytest.mark.parametrize("field", MANDATORY)
def test_pass_impossible_for_every_missing_mandatory_capability(field):
    full = claimed_complete()
    audit = replace(full, capabilities=tuple(c for c in full.capabilities if c.name != field))
    assert audit.to_payload()["final_status"] == "BLOCKED_WALLET_DATA_SOURCE"
    assert any(b["field"] == field and b["code"] == "FIELD_MISSING" for b in audit.blockers)


def test_fixture_and_documentation_cannot_establish_real_causal_history():
    full = claimed_complete()
    assert full.to_payload()["final_status"] == "BLOCKED_WALLET_DATA_SOURCE"
    documented = replace(full, provenance=(replace(full.provenance[0], fixture=False, kind="DOCUMENTATION"),))
    assert any(b["field"] == "causal_history" for b in documented.blockers)
    assert documented.to_payload()["final_status"] == "BLOCKED_WALLET_DATA_SOURCE"


def test_verified_labels_without_actual_timing_witness_cannot_pass():
    full = claimed_complete()
    labels_only = replace(full, provenance=(replace(full.provenance[0], fixture=False),), causal_witnesses=())
    assert any(b["field"] == "causal_witness" for b in labels_only.blockers)
    for field in ("event_id", "event_time", "observed_at", "finality_observed_at"):
        missing = replace(labels_only, causal_witnesses=(CausalWitness(observation(**{field: None}), "test", PAST),))
        assert missing.to_payload()["final_status"] == "BLOCKED_WALLET_DATA_SOURCE"


@pytest.mark.parametrize("key,value", [("final_status", "PASS_READ_ONLY_SOURCE"), ("live_enabled", True), ("mandatory_capabilities", [])])
def test_input_cannot_override_policy(key, value):
    with pytest.raises(ValueError, match="override"):
        load_audit({key: value})


def test_unknown_fields_not_invented_and_evidence_references_validated():
    result = load_audit({}).to_payload()
    assert result["source_id"] is None and result["venue_product"] is None
    assert len(result["blockers"]) >= len(MANDATORY)
    with pytest.raises(ValueError, match="unresolved"):
        CapabilityAudit("s", "v", {}, (Capability("event_time", State.VERIFIED, "x", ("absent",)),), ())
    full = claimed_complete()
    with pytest.raises(ValueError, match="duplicate"):
        replace(full, capabilities=full.capabilities + (full.capabilities[0],))


def test_runtime_audit_import_and_execution_tripwire(tmp_path):
    # Fresh interpreter: importing an SDK, inference runner or starting any
    # network/process action is an immediate test failure, even with LIVE env.
    script = r'''
import sys, runpy
def deny(event, args):
    if event == "import" and args[0].split(".")[0] in {
        "veranta_sdk", "avantis_trader_sdk", "web3", "eth_account", "vector_cannon"
    }:
        raise AssertionError("FORBIDDEN_CLIENT_IMPORT: " + args[0])
    if event.startswith("socket.") or event in {"subprocess.Popen", "os.system", "os.exec"}:
        raise AssertionError("FORBIDDEN_SIDE_EFFECT: " + event)
sys.addaudithook(deny)
sys.path.insert(0, sys.argv[1])
sys.argv = ["s01", "audit", "--evidence", sys.argv[2], "--out", sys.argv[3], "--repo-root", sys.argv[4]]
runpy.run_module("w12_edge_miner.s01", run_name="__main__")
'''
    import os
    env = dict(os.environ, LIVE_ENABLED="true", VERANTA_PRIVATE_KEY="synthetic-tripwire-not-a-key")
    result = subprocess.run([sys.executable, "-c", script, str(ROOT / "src"), str(EVIDENCE),
                             str(tmp_path / "reports"), str(ROOT)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "BLOCKED_WALLET_DATA_SOURCE"


def test_s01_static_import_boundary():
    allowed = {"__future__", "argparse", "dataclasses", "datetime", "enum", "hashlib", "json",
               "pathlib", "time", "typing", "urllib"}
    for path in (ROOT / "src/w12_edge_miner").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                assert all(n.name.split(".")[0] in allowed for n in node.names), path
            if isinstance(node, ast.ImportFrom) and not node.level:
                assert node.module.split(".")[0] in allowed, path
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"eval", "exec", "__import__", "getattr"}, path


@pytest.mark.parametrize("name", list(ENDPOINTS))
def test_probe_only_sends_fixed_public_get_without_credentials(name, monkeypatch):
    calls = []
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, n):
            assert n == 2_000_001
            return b'{}'
    class Opener:
        def open(self, request, timeout):
            assert request.full_url == ENDPOINTS[name]
            assert request.get_method() == "GET" and request.data is None
            assert not ({"authorization", "cookie"} & {h.lower() for h in request.headers})
            assert timeout == 12
            calls.append(request)
            return Response()
    def opener(handler):
        assert isinstance(handler, NoRedirect)
        assert handler.redirect_request(None, None, 302, None, None, "https://invalid/order") is None
        return Opener()
    monkeypatch.setattr("urllib.request.build_opener", opener)
    monkeypatch.setenv("VERANTA_API_BASE_URL", "https://invalid/order")
    monkeypatch.setenv("LIVE_ENABLED", "true")
    receipt = public_probe(name)
    assert len(calls) == 1 and receipt["outcome"] == "READ_ONLY_RESPONSE"
    assert receipt["observed_at"] >= receipt["requested_at"]
    with pytest.raises(ValueError, match="not allowed"):
        public_probe("https://invalid/order")


@pytest.mark.parametrize("code,outcome", [(401, "AUTH_REQUIRED"), (403, "ACCESS_DENIED"),
                                          (404, "SOURCE_UNSUPPORTED"), (429, "RATE_LIMITED")])
def test_probe_classifies_failures_without_retry(code, outcome, monkeypatch):
    class Opener:
        calls = 0
        def open(self, request, timeout):
            self.calls += 1
            raise urllib.error.HTTPError(request.full_url, code, "test", {}, None)
    op = Opener()
    monkeypatch.setattr("urllib.request.build_opener", lambda *_: op)
    assert public_probe("app_status")["outcome"] == outcome
    assert op.calls == 1


def test_probe_network_failure_is_not_source_unsupported(monkeypatch):
    class Opener:
        def open(self, *args, **kwargs):
            raise urllib.error.URLError("synthetic offline")
    monkeypatch.setattr("urllib.request.build_opener", lambda *_: Opener())
    assert public_probe("app_status")["outcome"] == "NETWORK_UNAVAILABLE"


def test_frozen_original_lock_is_checked_and_never_written(tmp_path):
    w11, w12 = tmp_path / "w11", tmp_path / "w12"
    (w11 / "reports/w11/run").mkdir(parents=True)
    w12.mkdir()
    canonical = w11 / "reports/w11/run/PREREGISTRATION.json"
    canonical.write_text('{"frozen":true}')
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    lock = w12 / "W12_BASELINE_LOCK.json"
    lock.write_text(encoded({"run_trees": {"run": {"files": {"PREREGISTRATION.json": digest(canonical)}}},
                             "workspace_src_tree": {"files": {}}, "workspace_tests_tree": {"files": {}}}))
    pins = [{"root": "w12", "path": lock.name, "sha256": digest(lock)}]
    before = (canonical.read_bytes(), lock.read_bytes())
    result = verify_frozen(pins, {"w11": w11, "w12": w12})
    assert result["status"] == "PASS" and result["checked_files"] == 2
    assert (canonical.read_bytes(), lock.read_bytes()) == before
    canonical.write_text('{"frozen":false}')
    assert verify_frozen(pins, {"w11": w11, "w12": w12})["status"] == "FAIL"
    lock.write_text('{}')
    assert verify_frozen(pins, {"w11": w11, "w12": w12})["status"] == "FAIL"
    assert verify_frozen(pins, {})["status"] == "UNVERIFIED"
    with pytest.raises(ValueError, match="outside"):
        verify_frozen([{"root": "w12", "path": "../escape", "sha256": "a" * 64}], {"w12": w12})


def test_report_deterministic_and_cannot_overwrite_inputs(tmp_path):
    before = EVIDENCE.read_bytes()
    roots = {"repo": ROOT}
    first = run(EVIDENCE, tmp_path / "first", roots)
    second = run(EVIDENCE, tmp_path / "second", roots)
    assert first == second
    assert EVIDENCE.read_bytes() == before
    for name in ("wallet_data_capability.json", "WALLET_DATA_CAPABILITY_AUDIT.md"):
        assert (tmp_path / "first" / name).read_bytes() == (tmp_path / "second" / name).read_bytes()
    assert run(EVIDENCE, tmp_path / "first", roots) == first
    changed = tmp_path / "first/wallet_data_capability.json"
    changed.write_text("existing artifact")
    with pytest.raises(FileExistsError):
        run(EVIDENCE, tmp_path / "first", roots)
    assert changed.read_text() == "existing artifact"
    with pytest.raises(ValueError, match="frozen"):
        run(EVIDENCE, tmp_path / "second", {"repo": ROOT, "w11": tmp_path})


def test_repository_canonical_specs_unchanged():
    pins = json.loads(EVIDENCE.read_text())["frozen_pins"]
    assert {p["path"] for p in pins if p["root"] == "repo"} == {
        "docs/CODEX_W12_EDGE_MINER_S01.md", "docs/W12_EDGE_MINER_V1.md"}
    for pin in pins:
        if pin["root"] == "repo":
            assert hashlib.sha256((ROOT / pin["path"]).read_bytes()).hexdigest() == pin["sha256"]


def test_real_captured_evidence_is_blocked_and_all_questions_answered():
    data = json.loads(EVIDENCE.read_text())
    result = load_audit(data).to_payload()
    assert result["final_status"] == "BLOCKED_WALLET_DATA_SOURCE"
    assert {q["number"] for q in data["audit_questions"]} == set(range(1, 18))
    assert all(q["answer"] for q in data["audit_questions"])
    assert data["scope"]["chain_id"] == 8453
    assert set(data["scope"]["markets"]) == {"BTC_UPSIDE/USD", "ETH_UPSIDE/USD", "SOL_UPSIDE/USD"}
    assert not data.get("causal_witnesses")
