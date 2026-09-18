from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from vector_cannon.launcher import (
    BASE_COMMIT, W02_PATCH_SHA256, W02_SOURCE_TREE, CodexCLI, main, run_ticket,
)

PROJECT = Path(__file__).resolve().parents[1]
MOCK = r'''#!PYTHON
import json, os, signal, subprocess, sys, time
from pathlib import Path
if sys.argv[1:] == ["exec", "--help"]:
    print("exec --sandbox --json --output-schema --output-last-message; stdin: -")
    sys.exit(0)
assert sys.argv[1] == "exec" and sys.argv[-1] == "-"
assert sys.argv[sys.argv.index("--sandbox") + 1] == "workspace-write"
response = Path(sys.argv[sys.argv.index("--output-last-message") + 1])
out = response.parent
with (out / "launches").open("a") as handle:
    handle.write(str(os.getpid()) + "\n")
payload = json.load(sys.stdin)
(out / "received.json").write_text(json.dumps(payload))
task = payload["ticket"]
mode = task["instructions"]
(out / "started").write_text("started")
if mode == "nonzero":
    print("deliberate failure", file=sys.stderr)
    sys.exit(7)
if mode == "timeout":
    child = subprocess.Popen([sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"])
    (out / "child.pid").write_text(str(child.pid))
    time.sleep(30)
if mode == "slow":
    time.sleep(1.0)
if mode == "git-tamper":
    Path(".git/config").write_text("[core]\nrepositoryformatversion = 0\n")
if mode == "tamper-verifier":
    (out / "verification-workspace" / "pyproject.toml").write_text("tampered")
if mode not in {"no-patch", "blocked-no-patch"}:
    target = Path("fixture-output.txt")
    target.write_text("changed by mock\n")
if mode == "unauthorized":
    Path("forbidden.txt").write_text("outside allowed files")
if mode == "ignored-unauthorized":
    Path("__pycache__").mkdir()
    Path("__pycache__/hidden.pyc").write_bytes(b"hidden change")
if mode == "symlink":
    Path("fixture-output.txt").unlink()
    Path("fixture-output.txt").symlink_to("pyproject.toml")
if mode == "secret-output":
    print("sk-abcdefghijklmnopqrstuv")
if mode == "import-source":
    Path("src/fixture_module.py").write_text("VALUE = 'patched-source'\n")
if mode != "no-report":
    response.write_text(json.dumps({"task_id": task["task_id"], "status": "blocked" if mode in {"blocked", "blocked-no-patch"} else "completed", "summary": "Tests passed (an untrusted worker claim)."}))
print(json.dumps({"type": "mock.completed", "mode": mode}))
'''


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    """Reconstruct W02 from the real pinned worktree, without creating commits."""
    root = tmp_path_factory.mktemp("launcher-baseline")
    patch = root / "w02.patch"
    patch.write_bytes(git(PROJECT, "diff", "--binary", "--full-index", BASE_COMMIT, "--",
                          "src/vector_cannon/inference_meter.py", "tests/test_inference_meter.py"))
    assert hashlib.sha256(patch.read_bytes()).hexdigest() == W02_PATCH_SHA256
    repo = root / "parent"
    git(PROJECT, "clone", "--local", "--no-hardlinks", "--no-checkout", str(PROJECT), str(repo))
    git(repo, "checkout", "--detach", BASE_COMMIT)
    git(repo, "apply", "--check", str(patch))
    git(repo, "apply", str(patch))
    return repo, patch


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("launcher acceptance tests must not use the network")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.fixture
def setup(tmp_path, baseline):
    executable = tmp_path / "fake-codex"
    executable.write_text(MOCK.replace("#!PYTHON", "#!" + sys.executable))
    executable.chmod(0o700)
    ticket = {
        "task_id": "fixture-shot", "instructions": "success", "target": "codex-cli",
        "base_commit": BASE_COMMIT, "w02_patch_sha256": W02_PATCH_SHA256,
        "allowed_files": ["fixture-output.txt"],
        "verification_commands": [[sys.executable, "-c",
            "from pathlib import Path; assert Path('fixture-output.txt').read_text() == 'changed by mock\\n'; print('VERIFIED BY CONTROLLER')"]],
        "timeout_seconds": 10, "artifact_dir": str(tmp_path / "artifacts"),
    }
    return {"ticket": ticket, "repo": baseline[0], "w02_patch": baseline[1],
            "adapter": CodexCLI(str(executable), simulated=True), "_lock_path": tmp_path / "single.lock"}


def run(setup, **ticket_changes):
    options = dict(setup)
    ticket = dict(options.pop("ticket"), **ticket_changes)
    return run_ticket(ticket, execute=True, **options)


def run_dir(setup):
    ticket = setup["ticket"]
    return Path(ticket["artifact_dir"]) / ticket["task_id"]


def assert_one_launch(setup, result):
    assert result["launch_count"] == 1
    assert len((run_dir(setup) / "launches").read_text().splitlines()) == 1


def parent_state(repo):
    return git(repo, "diff", "--binary", BASE_COMMIT), git(repo, "status", "--porcelain"), git(repo, "rev-parse", "HEAD")


def test_l1_dry_run_does_not_start_target(setup):
    options = dict(setup)
    result = run_ticket(options.pop("ticket"), **options)
    assert result["state"] == "NOT_STARTED"
    assert result["stop_reason"] == "DRY_RUN"
    assert result["launch_count"] == 0
    assert result["plan"]["argv"][-1] == "-"
    assert result["plan"]["cwd"] != str(setup["repo"])
    assert not run_dir(setup).exists()  # Not even a help probe in dry-run.


def test_l2_success_collects_real_files_patch_and_controller_verification(setup):
    before = parent_state(setup["repo"])
    result = run(setup)
    assert result["state"] == "SUCCEEDED", result
    assert_one_launch(setup, result)
    received = json.loads((run_dir(setup) / "received.json").read_text())["ticket"]
    assert received["instructions"] == "success"
    assert received["allowed_files"] == ["fixture-output.txt"]
    assert received["w02_patch_sha256"] == W02_PATCH_SHA256
    assert result["changed_files"] == ["fixture-output.txt"]
    assert result["baseline"]["source_tree"] == W02_SOURCE_TREE
    patch = Path(result["artifacts"]["patch"])
    assert "new file mode" in patch.read_text()
    # Independent apply check against untouched parent; this does not apply it there.
    git(setup["repo"], "apply", "--check", str(patch))
    checks = json.loads(Path(result["artifacts"]["verification"]).read_text())
    assert checks[0]["argv"] == setup["ticket"]["verification_commands"][0]
    assert checks[0]["exit_code"] == 0 and checks[0]["source"] == "CONTROLLER"
    assert "VERIFIED BY CONTROLLER" in Path(checks[0]["stdout"]).read_text()
    assert checks[0]["cwd"] != result["plan"]["cwd"]
    assert result["exit_code"] == 0 and result["started_at"] and result["ended_at"]
    assert [step["state"] for step in result["history"]] == ["NOT_STARTED", "RUNNING", "SUCCEEDED"]
    assert json.loads(Path(result["artifacts"]["result"]).read_text())["state"] == "SUCCEEDED"
    assert parent_state(setup["repo"]) == before
    assert not (setup["repo"] / "fixture-output.txt").exists()


def test_l3_nonzero_is_failed_without_retry(setup):
    result = run(setup, instructions="nonzero")
    assert result["state"] == "FAILED"
    assert result["exit_code"] == 7
    assert result["stop_reason"] == "NONZERO_EXIT"
    assert result["verification"] == []
    assert_one_launch(setup, result)


def process_running(pid):
    path = Path(f"/proc/{pid}/stat")
    if not path.exists():
        return False
    return path.read_text().split(") ", 1)[1].split()[0] != "Z"


@pytest.mark.skipif(os.name != "posix" or not Path("/proc").is_dir(), reason="POSIX/Linux process evidence")
def test_l4_timeout_stops_worker_and_its_term_ignoring_child(setup):
    result = run(setup, instructions="timeout", timeout_seconds=0.5)
    assert result["state"] == "TIMED_OUT", result
    assert result["stop_reason"] == "EXECUTION_TIME_LIMIT"
    child = int((run_dir(setup) / "child.pid").read_text())
    deadline = time.monotonic() + 2
    while process_running(child) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not process_running(child)
    assert not process_running(result["execution"]["pid"])
    assert result["execution"]["local_process_reaped"] is True
    assert result["external_processing_stop"] == "UNKNOWN"
    assert result["external_billing_stop"] == "UNKNOWN"
    assert_one_launch(setup, result)


def test_l5_separate_process_requests_share_single_lock(setup, tmp_path):
    first_ticket = dict(setup["ticket"], instructions="slow")
    ticket_file = tmp_path / "first-ticket.json"
    ticket_file.write_text(json.dumps(first_ticket))
    result_file = tmp_path / "first-result.json"
    script = (
        "import json,sys; from pathlib import Path; from vector_cannon.launcher import CodexCLI,run_ticket; "
        "r=run_ticket(json.loads(Path(sys.argv[1]).read_text()),repo=Path(sys.argv[2]),"
        "w02_patch=Path(sys.argv[3]),execute=True,adapter=CodexCLI(sys.argv[4],True),_lock_path=Path(sys.argv[5])); "
        "Path(sys.argv[6]).write_text(json.dumps(r))"
    )
    first = subprocess.Popen([sys.executable, "-c", script, str(ticket_file), str(setup["repo"]),
                              str(setup["w02_patch"]), setup["adapter"].executable,
                              str(setup["_lock_path"]), str(result_file)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=dict(os.environ, PYTHONPATH=str(PROJECT / "src")))
    try:
        deadline = time.monotonic() + 10
        while not (run_dir(setup) / "started").exists() and time.monotonic() < deadline and first.poll() is None:
            time.sleep(0.02)
        assert (run_dir(setup) / "started").exists(), first.communicate(timeout=2)
        second = run(setup, task_id="second-shot", artifact_dir=str(tmp_path / "other-artifacts"))
        assert second["state"] == "BLOCKED", second
        assert second["stop_reason"] == "ANOTHER_LAUNCH_RUNNING"
        assert second["launch_count"] == 0
        stdout, stderr = first.communicate(timeout=10)
        assert first.returncode == 0, (stdout, stderr)
        result = json.loads(result_file.read_text())
        assert result["state"] == "SUCCEEDED", result
        assert_one_launch(setup, result)
        assert not (tmp_path / "other-artifacts").exists()
    finally:
        if first.poll() is None:
            first.kill()
            first.wait()


@pytest.mark.parametrize("mode,violation", [("unauthorized", "forbidden.txt"),
    ("ignored-unauthorized", "__pycache__/hidden.pyc"), ("symlink", "fixture-output.txt"),
    ("git-tamper", ".git"), ("tamper-verifier", "verification-workspace")])
def test_l6_scope_violation_rejected_and_parent_untouched(setup, mode, violation):
    before = parent_state(setup["repo"])
    result = run(setup, instructions=mode)
    assert result["state"] == "FAILED"
    assert result["stop_reason"] == "CHANGES_OUTSIDE_ALLOWED_FILES"
    assert violation in result["scope_violations"]
    assert result["verification"] == []
    assert parent_state(setup["repo"]) == before
    assert_one_launch(setup, result)


@pytest.mark.parametrize("change,reason", [
    ({"base_commit": "0" * 40}, "BASELINE_IDENTITY_MISMATCH"),
    ({"w02_patch_sha256": "0" * 64}, "BASELINE_IDENTITY_MISMATCH"),
    ({"target": "another-ai"}, "INVALID_TARGET"),
    ({"instructions": ""}, "EMPTY_INSTRUCTIONS"),
    ({"allowed_files": ["../outside"]}, "INVALID_ALLOWED_PATH"),
    ({"allowed_files": [".git/config"]}, "INVALID_ALLOWED_PATH"),
    ({"verification_commands": ["echo pass"]}, "VERIFICATION_REQUIRES_ARGV_ARRAYS"),
    ({"verification_commands": []}, "MISSING_VERIFICATION_COMMANDS"),
    ({"timeout_seconds": float("nan")}, "INVALID_TIMEOUT"),
    ({"timeout_seconds": 0}, "INVALID_TIMEOUT"),
])
def test_l7_invalid_input_blocked_before_worker(setup, change, reason):
    result = run(setup, **change)
    assert result["state"] == "BLOCKED"
    assert result["stop_reason"] == reason
    assert result["launch_count"] == 0
    assert not run_dir(setup).exists()


def test_l7_required_field_missing(setup):
    setup["ticket"].pop("instructions")
    result = run(setup)
    assert result["state"] == "BLOCKED"
    assert result["stop_reason"] == "MISSING_FIELDS:instructions"
    assert result["launch_count"] == 0


def test_l7_incorrect_patch_preserves_existing_changes(setup, tmp_path):
    before = parent_state(setup["repo"])
    patch = tmp_path / "incorrect.patch"
    patch.write_bytes(setup["w02_patch"].read_bytes() + b"\n")
    setup["w02_patch"] = patch
    result = run(setup)
    assert result["state"] == "BLOCKED"
    assert result["stop_reason"] == "W02_PATCH_HASH_MISMATCH"
    assert result["launch_count"] == 0
    assert parent_state(setup["repo"]) == before


def test_l7_w02_missing_in_source_does_not_reset_or_launch(setup, tmp_path):
    clone = tmp_path / "base-only"
    git(setup["repo"], "clone", "--local", "--no-hardlinks", str(setup["repo"]), str(clone))
    setup["repo"] = clone
    before = parent_state(clone)
    result = run(setup)
    assert result["state"] == "BLOCKED"
    assert result["stop_reason"] == "W02_WORKTREE_MISMATCH"
    assert result["launch_count"] == 0
    assert parent_state(clone) == before


@pytest.mark.parametrize("mode,reason", [("no-patch", "PATCH_MISSING_OR_EMPTY"),
                                       ("no-report", "WORKER_REPORT_MISSING")])
def test_l8_zero_exit_without_required_artifacts_is_not_success(setup, mode, reason):
    result = run(setup, instructions=mode)
    assert result["state"] == "FAILED", result
    assert result["exit_code"] == 0
    assert result["stop_reason"] == reason
    assert result["verification"] == []
    assert_one_launch(setup, result)


def test_l8_worker_test_claim_does_not_replace_failing_controller_test(setup):
    result = run(setup, verification_commands=[[sys.executable, "-c", "raise SystemExit(23)"]])
    assert result["state"] == "FAILED"
    assert "Tests passed" in result["worker_claim"]["summary"]
    assert result["stop_reason"] == "VERIFICATION_FAILED"
    assert result["verification"][0]["exit_code"] == 23
    assert result["verification"][0]["source"] == "CONTROLLER"
    assert_one_launch(setup, result)


@pytest.mark.parametrize("mode", ["blocked", "blocked-no-patch"])
def test_l8_worker_blockage_is_preserved(setup, mode):
    result = run(setup, instructions=mode)
    assert result["state"] == "BLOCKED"
    assert result["stop_reason"] == "WORKER_REPORTED_BLOCKED"
    assert result["verification"] == []
    assert result["worker_claim"]["status"] == "blocked"
    assert_one_launch(setup, result)


def test_l8_verification_timeout_is_not_success(setup):
    result = run(setup, timeout_seconds=0.5,
                 verification_commands=[[sys.executable, "-c", "import time; time.sleep(30)"]])
    assert result["state"] == "TIMED_OUT"
    assert result["stop_reason"] == "VERIFICATION_TIME_LIMIT"
    assert result["verification"][0]["timed_out"]
    assert_one_launch(setup, result)


def observations(tmp_path, *, mixed=False, missing=False):
    common = {"launcher": "codex-cli", "shot_id": "fixture-shot", "metric_kind": "quota", "unit": "percent",
              "status": "MEASURED", "limit_value": 100, "source_method": "fixture",
              "source_ref": "fixture://W03", "source_scale": "fixture-quota-scale", "provider": "fixture",
              "plan_id": "fixture-plan", "scope_id": "fixture-scope", "period_id": "fixture-period",
              "period_start": "2026-09-15T00:00:00+00:00", "period_end": "2026-09-16T00:00:00+00:00",
              "reset_id": "fixture-reset", "reset_detected": False, "concurrent_usage": None,
              "exclusive_evidence": None, "recorded_at": "2026-09-15T02:00:00+00:00"}
    start = dict(common, phase="start", remaining_value=80, timestamp="2026-09-15T00:00:00+00:00")
    end = dict(common, phase="end", remaining_value=70, timestamp="2026-09-15T01:00:00+00:00")
    if mixed:
        end.update(metric_kind="cost", unit="USD")
    if missing:
        start["timestamp"] = None
    path = tmp_path / "observations.jsonl"
    path.write_text(json.dumps(start) + "\n" + json.dumps(end) + "\n")
    return path


@pytest.mark.parametrize("mixed,missing", [(False, False), (True, False), (False, True)])
def test_l9_w02_observations_preserve_unknown_and_distinct_metrics(setup, tmp_path, mixed, missing):
    path = observations(tmp_path, mixed=mixed, missing=missing)
    before = path.read_bytes()
    result = run(setup, observations_file=str(path))
    assert result["state"] == "SUCCEEDED", result
    usage = result["usage"]
    assert usage["budget_within_limit"] == "UNKNOWN"
    assert usage["status"] == "UNKNOWN"
    assert len(usage["observations"]) == 2
    assert usage["observations"][0]["source_ref"] == "fixture://W03"
    assert usage["observations"][1]["source_method"] == "fixture"
    assert all(axis["value"] is None for axis in usage["axes"].values())
    assert all(axis["status"] == "UNKNOWN" for axis in usage["axes"].values())
    assert usage["shots"][0]["reasons"]
    if not mixed and not missing:
        assert usage["axes"]["quota"]["display_delta"] == 10
        assert usage["axes"]["quota"]["unit"] == "percentage_points"
    if mixed:
        assert {row["metric_kind"] for row in usage["observations"]} == {"quota", "cost"}
        assert usage["shots"][0]["display_delta"] is None
    if missing:
        start = next(row for row in usage["observations"] if row["phase"] == "start")
        assert start["timestamp"] is None
    assert path.read_bytes() == before
    saved = json.loads(Path(result["artifacts"]["result"]).read_text())
    assert saved["usage"] == usage


def test_l9_no_observations_explicitly_unknown(setup):
    result = run(setup)
    assert result["state"] == "SUCCEEDED"
    assert result["usage"]["observations"] == []
    assert result["usage"]["budget_within_limit"] == "UNKNOWN"
    for axis in result["usage"]["axes"].values():
        assert axis["value"] is None and axis["status"] == "UNKNOWN"
        assert axis["reasons"] == ["OBSERVATIONS_NOT_PROVIDED"]


def test_l10_simulation_does_not_verify_real_ai(setup):
    result = run(setup)
    assert result["state"] == "SUCCEEDED"
    assert result["simulation"] is True
    assert result["launch_target"]["mode"] == "SIMULATED"
    assert result["launch_target"]["local_help"] == "SIMULATED"
    assert result["real_connection"]["mode"] == "REAL"
    assert result["real_connection"]["local_help"] == "UNVERIFIED"
    assert result["real_connection"]["real_ai_connection"] == "UNVERIFIED"


def test_l10_missing_cli_is_blocked_without_fallback(setup, tmp_path):
    setup["adapter"] = CodexCLI(str(tmp_path / "not-installed"))
    result = run(setup)
    assert result["state"] == "BLOCKED"
    assert result["stop_reason"] == "CLI_NOT_FOUND"
    assert result["real_connection"]["availability"] == "BLOCKED"
    assert result["real_connection"]["real_ai_connection"] == "UNVERIFIED"
    assert result["launch_count"] == 0
    assert not run_dir(setup).exists()


def test_l10_unverified_cli_syntax_blocks_task_execution(setup, tmp_path):
    bad = tmp_path / "bad-help"
    bad.write_text("#!" + sys.executable + "\nprint('unknown syntax')\n")
    bad.chmod(0o700)
    setup["adapter"] = CodexCLI(str(bad), simulated=True)
    result = run(setup)
    assert result["state"] == "BLOCKED"
    assert result["stop_reason"] == "CLI_SYNTAX_UNVERIFIED"
    assert result["launch_count"] == 0
    assert not (run_dir(setup) / "launches").exists()


def test_l1_cli_defaults_to_dry_run_json(setup, tmp_path, capsys):
    ticket = tmp_path / "ticket.json"
    ticket.write_text(json.dumps(setup["ticket"]))
    code = main(["--ticket", str(ticket), "--repo", str(setup["repo"]),
                 "--w02-patch", str(setup["w02_patch"]), "--codex-path", setup["adapter"].executable])
    result = json.loads(capsys.readouterr().out)
    assert code == 0 and result["state"] == "NOT_STARTED"
    assert result["dry_run"] is True and result["launch_count"] == 0
    assert result["real_connection"]["real_ai_connection"] == "UNVERIFIED"
    assert not run_dir(setup).exists()


def test_l1_cli_legacy_landlock_is_opt_in_in_dry_run(setup, tmp_path, capsys):
    ticket = tmp_path / "legacy-ticket.json"
    ticket.write_text(json.dumps(setup["ticket"]))
    code = main(["--ticket", str(ticket), "--repo", str(setup["repo"]),
                 "--w02-patch", str(setup["w02_patch"]), "--codex-path",
                 setup["adapter"].executable, "--legacy-landlock"])
    result = json.loads(capsys.readouterr().out)
    assert code == 0 and result["state"] == "NOT_STARTED"
    assert result["dry_run"] is True and result["launch_count"] == 0
    assert result["launch_target"]["linux_sandbox_backend"] == "LEGACY_LANDLOCK"
    assert result["plan"]["argv"][1:4] == ["--enable", "use_legacy_landlock", "exec"]
    assert not run_dir(setup).exists()


def test_l2_logs_redact_recognized_credentials(setup):
    result = run(setup, instructions="secret-output")
    assert result["state"] == "SUCCEEDED"
    stdout = Path(result["artifacts"]["stdout"]).read_text()
    assert "sk-abcdefghijklmnopqrstuv" not in stdout
    assert "[REDACTED_SECRET]" in stdout


def test_l8_unavailable_verification_keeps_null_exit_code(setup, tmp_path):
    result = run(setup, verification_commands=[[str(tmp_path / "missing-test-command")]])
    assert result["state"] == "FAILED"
    assert result["stop_reason"] == "VERIFICATION_FAILED"
    assert result["verification"][0]["started"] is False
    assert result["verification"][0]["exit_code"] is None
    assert result["verification"][0]["reason"] == "PROCESS_START_FAILED"
    assert_one_launch(setup, result)


def test_l2_verification_imports_the_patched_copy(setup, tmp_path, monkeypatch):
    wrong_source = tmp_path / "wrong-source"
    wrong_source.mkdir()
    (wrong_source / "fixture_module.py").write_text("VALUE = 'wrong-source'\n")
    monkeypatch.setenv("PYTHONPATH", str(wrong_source))
    result = run(setup, instructions="import-source",
                 allowed_files=["fixture-output.txt", "src/fixture_module.py"],
                 verification_commands=[[sys.executable, "-c",
                     "import fixture_module; assert fixture_module.VALUE == 'patched-source'"]])
    assert result["state"] == "SUCCEEDED", result
    check = result["verification"][0]
    assert check["environment_overrides"]["PYTHONPATH"] == str(Path(check["cwd"]) / "src")
