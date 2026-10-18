"""Offline, bounded reproduction of the previously unexplained local-only stop.

Uses synthetic artifacts and simulated adapters only. Do not relax the expected
stop or retry a failed mission to make this test green.
"""
from __future__ import annotations

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
