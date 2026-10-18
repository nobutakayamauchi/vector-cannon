"""Offline diagnostics for local-only stops and controller-owned Git snapshots.

Uses synthetic artifacts and simulated adapters only. Do not relax the expected
stop or retry a failed mission to make this test green. The historical failure
had no captured reason; a reproduced mechanism is not retrospective proof.
"""
from __future__ import annotations

import json
import time

import pytest

from test_dot_handoff_safety import make_engine
from vector_cannon.outcome import engine as engine_module
from vector_cannon.outcome.model import canonical


def test_local_only_repeated_sessions_preserve_authorization_stop(tmp_path):
    failures = []
    # Exercise the fast worker and small scheduling variations without provider
    # calls. Record the first cause, rather than losing it behind a state assertion.
    for iteration in range(100):
        case = tmp_path / f"case-{iteration:03d}"
        case.mkdir()
        metadata_observations = []
        git_calls = []
        with pytest.MonkeyPatch.context() as patch:
            real_metadata = engine_module.git_metadata
            real_git = engine_module.git

            def observe_metadata(root):
                result = real_metadata(root)
                previous = metadata_observations[-1]["values"] if metadata_observations else {}
                delta = sorted(k for k in previous.keys() | result.keys()
                               if previous.get(k) != result.get(k))
                metadata_observations.append({"values": result, "delta": delta})
                return result

            def observe_git(root, *args):
                git_calls.append(list(args))
                return real_git(root, *args)

            patch.setattr(engine_module, "git_metadata", observe_metadata)
            patch.setattr(engine_module, "git", observe_git)
            roles = () if iteration % 5 else ("jev", "sol", "astra")
            engine, agent, judges = make_engine(case, policy="local_only", roles=roles)
            execute = agent.execute

            def scheduled_execute(*args, **kwargs):
                time.sleep((0, 0.001, 0.005, 0.02)[iteration % 4])
                return execute(*args, **kwargs)

            patch.setattr(agent, "execute", scheduled_execute)
            report = engine.run()
            success = (
                report["state"] == "HUMAN_REQUIRED"
                and report["reason"] == "EXTERNAL_EVIDENCE_NOT_AUTHORIZED"
                and report["handoff"] is None
                and not (engine.store.directory / "handoff").exists()
                and all(judge.calls == 0 for judge in judges.values())
            )
            if not success:
                failures.append({
                    "iteration": iteration,
                    "report": report,
                    "git_calls": git_calls,
                    "metadata_deltas": [item["delta"] for item in metadata_observations[1:]],
                })
                # One captured failure is enough; never hide it by retrying.
                break
    assert not failures, canonical(failures)


def test_controller_git_does_not_launch_implicit_maintenance(tmp_path, monkeypatch):
    """Real local Git trace, not a mock claiming that maintenance was disabled."""
    root = tmp_path / "snapshot"
    root.mkdir()
    (root / "README.md").write_text("synthetic baseline\n")
    trace = tmp_path / "git-trace.jsonl"
    run = engine_module.subprocess.run

    def traced_run(argv, *args, **kwargs):
        if argv[0] == "git":
            # Test-only trace injection after the production environment filter.
            # No global Git configuration or user repository is changed.
            kwargs["env"] = {**kwargs["env"], "GIT_TRACE2_EVENT": str(trace)}
        return run(argv, *args, **kwargs)

    monkeypatch.setattr(engine_module.subprocess, "run", traced_run)
    engine_module.init_git(root)
    events = [json.loads(line) for line in trace.read_text().splitlines() if line]
    starts = [e.get("argv", []) for e in events if e.get("event") == "child_start"]
    unexpected = [argv for argv in starts if "maintenance" in argv or "gc" in argv]
    assert not unexpected, {"unexpected_implicit_git_children": unexpected}


def test_controller_maintenance_settings_do_not_mutate_repository_config(tmp_path):
    root = tmp_path / "snapshot"
    root.mkdir()
    engine_module.init_git(root)
    path = root / ".git" / "config"
    before = path.read_bytes()
    assert engine_module.git(root, "config", "--bool", "--get", "maintenance.auto").strip() == b"false"
    assert engine_module.git(root, "config", "--int", "--get", "gc.auto").strip() == b"0"
    assert path.read_bytes() == before
    assert b"maintenance" not in before


@pytest.mark.parametrize("name", ["config", "HEAD", "objects/maintenance.lock"])
def test_worker_git_mutations_still_block_even_for_maintenance_names(tmp_path, monkeypatch, name):
    engine, agent, _ = make_engine(tmp_path, policy="local_only")
    execute = agent.execute

    def tampering_execute(contract, workspace, *args, **kwargs):
        receipt = execute(contract, workspace, *args, **kwargs)
        target = workspace / ".git" / name
        with target.open("ab") as stream:
            stream.write(b"synthetic unauthorized worker mutation\n")
        return receipt

    monkeypatch.setattr(agent, "execute", tampering_execute)
    report = engine.run()
    assert report["state"] == "BLOCKED", report
    assert report["reason"] == "SCOPE_VIOLATION:.git", report
    assert report["handoff"] is None
    assert report["calls"]["worker"] == 1
    assert all(report["calls"][role] == 0 for role in ("jev", "sol", "astra"))
