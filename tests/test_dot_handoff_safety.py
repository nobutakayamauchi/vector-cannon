"""Offline regressions: only synthetic content; no provider calls or real credentials."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from test_dot_supervisor import ChoiceJudge, RecordingAgent, contract_payload
from vector_cannon.outcome.engine import OutcomeEngine
from vector_cannon.outcome.model import Contract, ControlError, canonical, digest
from vector_cannon.outcome.supervisor import build_dot_handoff, load_handoff
from vector_cannon.outcome.verify import decision_input, redact, verify


def make_engine(tmp_path, *, policy="redacted_evidence", roles=(), goal=None, content="ready\n"):
    raw = contract_payload()
    raw["data_policy"] = policy
    if goal is not None:
        raw["goal"] = goal
    raw["criteria"][0].update(kind="text_contains", expect="ready")
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("baseline\n")
    agent = RecordingAgent((content,))
    judges = {role: ChoiceJudge(role) for role in roles}
    engine = OutcomeEngine.create(Contract.parse(raw), source, tmp_path / "session", agent, judges)
    return engine, agent, judges


def rebuild(engine, state=None, **kwargs):
    state = copy.deepcopy(state or engine.store.load())
    return build_dot_handoff(engine.store, state, Contract.parse(state["contract"]),
                             reason=kwargs.get("reason", state["reason"]))


@pytest.mark.parametrize("roles", [(), ("jev",), ("sol",), ("jev", "sol", "astra")])
def test_local_only_stops_before_any_external_review_or_handoff(tmp_path, roles):
    engine, _, judges = make_engine(tmp_path, policy="local_only", roles=roles)
    report = engine.run()
    assert report["state"] == "HUMAN_REQUIRED"
    assert report["reason"] == "EXTERNAL_EVIDENCE_NOT_AUTHORIZED"
    assert report["handoff"] is None
    assert not (engine.store.directory / "handoff").exists()
    assert all(judge.calls == 0 for judge in judges.values())


def test_direct_builder_cannot_bypass_local_only(tmp_path):
    engine, _, _ = make_engine(tmp_path, policy="local_only")
    state = engine.store.load()
    with pytest.raises(ControlError, match="EXTERNAL_EVIDENCE_NOT_AUTHORIZED"):
        rebuild(engine, state)
    assert not (engine.store.directory / "handoff").exists()


@pytest.mark.parametrize("text,secret", [
    ("Review ready with api_key=fixture-api-value", "fixture-api-value"),
    ("Review ready with Bearer fixture-bearer-value", "fixture-bearer-value"),
    ("Review ready with password='fixture with spaces'", "fixture with spaces"),
    ('Review ready with {"token":"fixture-json-value"}', "fixture-json-value"),
    ("Review ready with sk-fixture0123456789", "sk-fixture0123456789"),
])
def test_goal_and_both_exports_redact_known_secret_forms(tmp_path, text, secret):
    engine, _, _ = make_engine(tmp_path, goal=text)
    report = engine.run()
    assert report["state"] == "DOT_HANDOFF"
    state = engine.store.load()
    before = canonical(state)
    payload = load_handoff(engine.store, state)
    assert secret not in canonical(payload)
    assert "[REDACTED]" in payload["goal"]
    for key in ("json", "markdown"):
        assert secret not in Path(report["handoff"][key]).read_text()
    assert canonical(state) == before
    assert state["contract"]["goal"] == text


def test_handoff_includes_criteria_and_bounded_artifact_content(tmp_path):
    content = "ready\nThis is the actual explanation the reviewer must inspect.\n"
    engine, _, _ = make_engine(tmp_path, content=content)
    report = engine.run()
    assert report["state"] == "DOT_HANDOFF"
    state = engine.store.load()
    handoff = load_handoff(engine.store, state)
    review = decision_input(Contract.parse(state["contract"]), state["evidence"])
    assert handoff["criteria"] == review["criteria"]
    assert handoff["checks"] == review["facts"]
    assert content in handoff["checks"][0]["excerpt"]
    assert handoff["evidence_hash"] == state["evidence"]["hash"]
    assert handoff["handoff_hash"] == digest({k: v for k, v in handoff.items() if k != "handoff_hash"})


def test_reason_and_prior_decisions_are_redacted_too(tmp_path):
    engine, _, _ = make_engine(tmp_path)
    engine.run()
    state = engine.store.load()
    state["decisions"] = [{"model": "Bearer fixture-model-value"}]
    # A fresh revision-bound name avoids replacing an existing, different handoff.
    state["candidate_revision"] += 1
    state["evidence"]["revision"] = state["candidate_revision"]
    state["evidence"]["hash"] = digest({k: v for k, v in state["evidence"].items() if k != "hash"})
    ref = rebuild(engine, state, reason="password=fixture-reason-value")
    for key in ("json", "markdown"):
        text = Path(ref[key]).read_text()
        assert "fixture-model-value" not in text
        assert "fixture-reason-value" not in text


def test_repeat_handoff_reuses_identical_private_files(tmp_path):
    engine, _, _ = make_engine(tmp_path)
    ref = engine.run()["handoff"]
    before = {key: Path(ref[key]).read_bytes() for key in ("json", "markdown")}
    for _ in range(3):
        assert rebuild(engine) == ref
    for key, data in before.items():
        assert Path(ref[key]).read_bytes() == data
        assert Path(ref[key]).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("missing", ["json", "markdown"])
def test_partial_publication_can_complete_without_replacing_other_file(tmp_path, missing):
    engine, _, _ = make_engine(tmp_path)
    ref = engine.run()["handoff"]
    expected = {key: Path(ref[key]).read_bytes() for key in ("json", "markdown")}
    Path(ref[missing]).unlink()
    assert rebuild(engine) == ref
    for key, data in expected.items():
        assert Path(ref[key]).read_bytes() == data


@pytest.mark.parametrize("kind", ["corrupt", "symlink", "hardlink"])
def test_existing_conflicting_or_linked_file_is_never_overwritten(tmp_path, kind):
    engine, _, _ = make_engine(tmp_path)
    ref = engine.run()["handoff"]
    path = Path(ref["json"])
    original = path.read_bytes()
    outside = tmp_path / "outside.json"
    outside.write_bytes(original)
    if kind == "corrupt":
        path.write_text("incomplete")
    else:
        path.unlink()
        if kind == "symlink":
            path.symlink_to(outside)
        else:
            path.hardlink_to(outside)
    before = path.read_bytes()
    with pytest.raises(ControlError, match="DOT_HANDOFF"):
        rebuild(engine)
    assert path.read_bytes() == before
    assert outside.read_bytes() == original


def test_symlink_handoff_directory_is_rejected(tmp_path):
    engine, _, _ = make_engine(tmp_path)
    engine.run()
    directory = engine.store.directory / "handoff"
    outside = tmp_path / "outside-directory"
    directory.rename(outside)
    directory.symlink_to(outside, target_is_directory=True)
    before = {p.name: p.read_bytes() for p in outside.iterdir()}
    with pytest.raises(ControlError, match="DOT_HANDOFF"):
        rebuild(engine)
    assert {p.name: p.read_bytes() for p in outside.iterdir()} == before


def test_resume_after_files_written_before_state_save_does_not_repeat_worker(tmp_path, monkeypatch):
    engine, agent, _ = make_engine(tmp_path)
    save = engine.store.save

    class SimulatedPowerLoss(BaseException):
        pass

    def interrupt(state, event):
        if event == "DOT_HANDOFF_CREATED":
            raise SimulatedPowerLoss()
        return save(state, event)

    monkeypatch.setattr(engine.store, "save", interrupt)
    with pytest.raises(SimulatedPowerLoss):
        engine.run()
    assert list((engine.store.directory / "handoff").glob("*.json"))
    assert engine.store.load()["phase"] == "JUDGE"
    monkeypatch.setattr(engine.store, "save", save)
    assert engine.run()["state"] == "DOT_HANDOFF"
    assert agent.calls == 1


@pytest.mark.parametrize("drift", ["revision", "snapshot", "hash"])
def test_handoff_rejects_evidence_not_bound_to_current_candidate(tmp_path, drift):
    engine, _, _ = make_engine(tmp_path)
    engine.run()
    state = engine.store.load()
    if drift == "revision":
        state["candidate_revision"] += 1
    elif drift == "snapshot":
        state["snapshot_hash"] = "0" * 64
    else:
        state["evidence"]["hash"] = "0" * 64
    with pytest.raises(ControlError, match="DOT_HANDOFF_REQUIRES_CURRENT_EVIDENCE"):
        rebuild(engine, state)


def test_loader_does_not_expose_legacy_local_only_handoff(tmp_path):
    engine, _, _ = make_engine(tmp_path)
    engine.run()
    state = engine.store.load()
    state["contract"]["data_policy"] = "local_only"
    with pytest.raises(ControlError, match="EXTERNAL_EVIDENCE_NOT_AUTHORIZED"):
        load_handoff(engine.store, state)


def test_structured_redaction_preserves_json_and_hides_secret_fields(tmp_path):
    engine, _, _ = make_engine(tmp_path)
    engine.run()
    state = engine.store.load()
    raw = state["contract"]
    raw["criteria"][0].update(kind="json_fields", expect={"token": "fixture-nested-value", "ok": True})
    raw["goal"] = 'Inspect ready\npassword="fixture quoted value"\nKeep this sentence.'
    review = decision_input(Contract.parse(raw), state["evidence"])
    serialized = canonical(review)
    assert json.loads(serialized) == review
    assert "fixture-nested-value" not in serialized
    assert "fixture quoted value" not in serialized
    assert "Keep this sentence." in review["goal"]
    assert review["criteria"][0]["expect"]["ok"] is True


def test_excerpt_is_redacted_before_truncation(tmp_path):
    engine, _, _ = make_engine(tmp_path)
    raw = engine.store.load()["contract"]
    root = tmp_path / "artifact"
    root.mkdir()
    (root / "out.txt").write_text("ready " + "x" * 1188 + " sk-fixture0123456789")
    evidence = verify(Contract.parse(raw), root, revision=0)
    assert "sk-" not in evidence["checks"][0]["excerpt"]


def test_oversize_handoff_is_rejected_before_publication(tmp_path):
    engine, _, _ = make_engine(tmp_path)
    engine.run()
    state = engine.store.load()
    state["decisions"] = [{"model": "x" * 70000}]
    directory = engine.store.directory / "handoff"
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    with pytest.raises(ControlError, match="DOT_HANDOFF_TOO_LARGE"):
        rebuild(engine, state)
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before
