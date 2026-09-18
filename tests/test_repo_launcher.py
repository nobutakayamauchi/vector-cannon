from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from vector_cannon.launcher import CodexCLI
from vector_cannon.repo_launcher import (
    RepositorySession,
    repository_identity,
    run_repo_ticket,
)


MOCK = r'''#!PYTHON
import json
import sys
from pathlib import Path

if sys.argv[1:] == ["exec", "--help"]:
    print("exec --sandbox --json --output-schema --output-last-message")
    raise SystemExit(0)

response = Path(sys.argv[sys.argv.index("--output-last-message") + 1])
payload = json.load(sys.stdin)
ticket = payload["ticket"]
mode = ticket["instructions"]

if mode == "unauthorized":
    Path("forbidden.txt").write_text("nope\n")
elif mode == "first":
    Path("state.txt").write_text("one\n")
elif mode == "second":
    current = Path("state.txt").read_text()
    Path("state.txt").write_text(current + "two\n")
else:
    Path("fixture-output.txt").write_text("VECTOR_CANNON_REPO_OK\n")

response.write_text(json.dumps({
    "task_id": ticket["task_id"],
    "status": "completed",
    "summary": "mock completed",
}))
print(json.dumps({"type": "mock.completed"}))
'''


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "source"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "README.md").write_text("baseline\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git", "-C", str(repo),
            "-c", "user.name=Fixture",
            "-c", "user.email=fixture@example.invalid",
            "commit", "-q", "-m", "baseline",
        ],
        check=True,
    )
    return repo


def make_codex(tmp_path: Path) -> Path:
    executable = tmp_path / "fake-codex"
    executable.write_text(MOCK.replace("#!PYTHON", "#!" + sys.executable))
    executable.chmod(0o700)
    return executable


def ticket(repo: Path, artifacts: Path, *, instructions: str = "normal") -> dict:
    return {
        "task_id": "repo-shot-1",
        "instructions": instructions,
        "base_commit": repository_identity(repo),
        "allowed_files": ["fixture-output.txt"],
        "verification_commands": [[
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('fixture-output.txt').read_text() == 'VECTOR_CANNON_REPO_OK\\n'",
        ]],
        "timeout_seconds": 10,
        "artifact_dir": str(artifacts),
    }


def test_repo_launcher_dry_run_does_not_create_workspace(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    fake = make_codex(tmp_path)
    artifacts = tmp_path / "artifacts"

    result = run_repo_ticket(
        ticket(repo, artifacts),
        repo=repo,
        adapter=CodexCLI(str(fake), simulated=True),
    )

    assert result["state"] == "NOT_STARTED"
    assert result["stop_reason"] == "DRY_RUN"
    assert result["launch_count"] == 0
    assert not artifacts.exists()


def test_repo_launcher_executes_in_clone_and_verifies_patch(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    fake = make_codex(tmp_path)
    artifacts = tmp_path / "artifacts"
    baseline = repository_identity(repo)

    result = run_repo_ticket(
        ticket(repo, artifacts),
        repo=repo,
        execute=True,
        adapter=CodexCLI(str(fake), simulated=True),
        _lock_path=tmp_path / "launcher.lock",
    )

    assert result["state"] == "SUCCEEDED"
    assert result["stop_reason"] == "PATCH_AND_CONTROLLER_VERIFICATION_PASSED"
    assert result["launch_count"] == 1
    assert result["changed_files"] == ["fixture-output.txt"]
    assert result["scope_violations"] == []
    assert result["patch_apply_check"] == "PASSED"
    assert result["verification"][0]["exit_code"] == 0
    assert Path(result["artifacts"]["patch"]).is_file()
    assert repository_identity(repo) == baseline
    assert not (repo / "fixture-output.txt").exists()


def test_repo_launcher_rejects_changes_outside_allowed_files(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    fake = make_codex(tmp_path)
    data = ticket(repo, tmp_path / "artifacts", instructions="unauthorized")

    result = run_repo_ticket(
        data,
        repo=repo,
        execute=True,
        adapter=CodexCLI(str(fake), simulated=True),
        _lock_path=tmp_path / "launcher.lock",
    )

    assert result["state"] == "FAILED"
    assert result["stop_reason"] == "CHANGES_OUTSIDE_ALLOWED_FILES"
    assert "forbidden.txt" in result["scope_violations"]


def test_repo_launcher_blocks_dirty_source_before_launch(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    fake = make_codex(tmp_path)
    data = ticket(repo, tmp_path / "artifacts")
    (repo / "dirty.txt").write_text("dirty\n")

    result = run_repo_ticket(
        data,
        repo=repo,
        execute=True,
        adapter=CodexCLI(str(fake), simulated=True),
        _lock_path=tmp_path / "launcher.lock",
    )

    assert result["state"] == "BLOCKED"
    assert result["launch_count"] == 0
    assert result["stop_reason"] == "SOURCE_REPOSITORY_NOT_CLEAN"


def test_repository_session_carries_successful_patch_into_next_shot(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    fake = make_codex(tmp_path)
    session = RepositorySession(
        source_repo=repo,
        session_dir=tmp_path / "session",
        adapter=CodexCLI(str(fake), simulated=True),
    )

    first = session.execute(
        task_id="job-s001",
        instructions="first",
        allowed_files=("state.txt",),
        verification_commands=((
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('state.txt').read_text() == 'one\\n'",
        ),),
        timeout_seconds=10,
    )
    second = session.execute(
        task_id="job-s002",
        instructions="second",
        allowed_files=("state.txt",),
        verification_commands=((
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('state.txt').read_text() == 'one\\ntwo\\n'",
        ),),
        timeout_seconds=10,
    )

    assert first["state"] == second["state"] == "SUCCEEDED"
    assert (session.staging_repo / "state.txt").read_text() == "one\ntwo\n"
    assert not (repo / "state.txt").exists()
    assert repository_identity(repo) == session.original_commit
    patch = session.cumulative_patch().decode()
    assert "state.txt" in patch
    assert "+one" in patch and "+two" in patch
