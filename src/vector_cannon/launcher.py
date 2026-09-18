"""One local Codex CLI launch; W02 observations are evidence, not a budget gate.

The workspace and post-run scope check are not an OS security boundary. The
flock coordinates launchers using this user's lock on one POSIX host only.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .inference_meter import load_snapshots, summarize_inference
from .repo_tools import SECRET_PATTERNS

BASE_COMMIT = "6416ab15a2a94ed9930a875eec0b899b633b186a"
W02_PATCH_SHA256 = "a25e18064fd76655542e861f30084b1ef01de9e650c1edf42d3c24fdcd6c3fe4"
W02_SOURCE_TREE = "4fd64d245107f305456e41dd41ca27f6727b19d1"  # tree, not a commit
W02_FILES = ("src/vector_cannon/inference_meter.py", "tests/test_inference_meter.py")
W03_FILES = ("src/vector_cannon/launcher.py", "tests/test_launcher.py")
CLI_DOC = "https://learn.chatgpt.com/docs/non-interactive-mode"
STATES = {"NOT_STARTED", "RUNNING", "SUCCEEDED", "FAILED", "TIMED_OUT", "BLOCKED"}
REPORT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "task_id": {"type": "string"},
        "status": {"type": "string", "enum": ["completed", "blocked"]},
        "summary": {"type": "string"},
    },
    "required": ["task_id", "status", "summary"],
}


class Blocked(ValueError):
    """A precondition prevents a launch; never retry automatically."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED_SECRET]", text)
    return re.sub(
        r"(?i)(authorization\s*[:=]\s*bearer\s+|(?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*)[^\s,\"}]+",
        r"\1[REDACTED_SECRET]", text,
    )


def _safe_json(value: Any) -> Any:
    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _safe_json(item) for key, item in value.items()}
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)  # retain invalid raw observations without JSON NaN
    return value


def _write_json(path: Path, value: Any) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(_safe_json(value), ensure_ascii=False, indent=2,
                               allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def _relative_file(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise Blocked("INVALID_ALLOWED_PATH")
    path = PurePosixPath(value)
    if (not path.parts or path.is_absolute() or str(path) != value or
            any(part in {".", "..", ".git"} for part in path.parts) or
            any(char in value for char in "*?[]\n\r")):
        raise Blocked("INVALID_ALLOWED_PATH")
    return value


@dataclass(frozen=True)
class Ticket:
    task_id: str
    instructions: str
    base_commit: str
    w02_patch_sha256: str
    allowed_files: tuple[str, ...]
    verification_commands: tuple[tuple[str, ...], ...]
    timeout_seconds: float
    artifact_dir: Path
    observations_file: Path | None = None
    observation_launcher: str = "codex-cli"
    target: str = "codex-cli"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Ticket:
        if not isinstance(data, dict):
            raise Blocked("INVALID_TICKET")
        required = {"task_id", "instructions", "base_commit", "w02_patch_sha256",
                    "allowed_files", "verification_commands", "timeout_seconds", "artifact_dir"}
        if required - data.keys():
            raise Blocked("MISSING_FIELDS:" + ",".join(sorted(required - data.keys())))
        if data.get("target", "codex-cli") != "codex-cli":
            raise Blocked("INVALID_TARGET")
        if not isinstance(data["task_id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", data["task_id"]):
            raise Blocked("INVALID_TASK_ID")
        if not isinstance(data["instructions"], str) or not data["instructions"].strip():
            raise Blocked("EMPTY_INSTRUCTIONS")
        if data["base_commit"] != BASE_COMMIT or data["w02_patch_sha256"] != W02_PATCH_SHA256:
            raise Blocked("BASELINE_IDENTITY_MISMATCH")
        allowed = data["allowed_files"]
        if not isinstance(allowed, list) or not allowed:
            raise Blocked("MISSING_ALLOWED_FILES")
        allowed = tuple(_relative_file(item) for item in allowed)
        if len(set(allowed)) != len(allowed):
            raise Blocked("DUPLICATE_ALLOWED_FILES")
        commands = data["verification_commands"]
        if not isinstance(commands, list) or not commands:
            raise Blocked("MISSING_VERIFICATION_COMMANDS")
        for argv in commands:
            if (not isinstance(argv, list) or not argv or
                    any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv)):
                raise Blocked("VERIFICATION_REQUIRES_ARGV_ARRAYS")
        timeout = data["timeout_seconds"]
        if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout <= 0:
            raise Blocked("INVALID_TIMEOUT")
        output = data["artifact_dir"]
        if not isinstance(output, str) or not Path(output).is_absolute():
            raise Blocked("ARTIFACT_DIR_MUST_BE_ABSOLUTE")
        observations = data.get("observations_file")
        if observations is not None and (not isinstance(observations, str) or not Path(observations).is_absolute()):
            raise Blocked("OBSERVATIONS_FILE_MUST_BE_ABSOLUTE")
        launcher = data.get("observation_launcher", "codex-cli")
        if not isinstance(launcher, str) or not launcher.strip():
            raise Blocked("INVALID_OBSERVATION_LAUNCHER")
        return cls(data["task_id"], data["instructions"], BASE_COMMIT, W02_PATCH_SHA256,
                   allowed, tuple(tuple(argv) for argv in commands), float(timeout),
                   Path(output).resolve(), Path(observations).resolve() if observations else None,
                   launcher)


def _git(repo: Path, *args: str, index: Path | None = None, work_tree: Path | None = None) -> bytes:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    argv = ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false",
            "-c", "core.autocrlf=false", "-c", "core.fileMode=true", "-C", str(repo)]
    if work_tree is not None:
        argv += ["--work-tree=" + str(work_tree)]
    completed = subprocess.run(argv + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=env, timeout=30, check=False)
    if completed.returncode:
        raise Blocked("GIT_ERROR:" + _redact(completed.stderr.decode(errors="replace").strip()))
    return completed.stdout


def _check_source(repo: Path, patch: Path) -> None:
    if _git(repo, "rev-parse", "HEAD").decode().strip() != BASE_COMMIT:
        raise Blocked("BASE_COMMIT_MISMATCH")
    if hashlib.sha256(patch.read_bytes()).hexdigest() != W02_PATCH_SHA256:
        raise Blocked("W02_PATCH_HASH_MISMATCH")
    diff = _git(repo, "diff", "--binary", "--full-index", "--no-ext-diff", "--no-textconv",
                BASE_COMMIT, "--", *W02_FILES)
    if hashlib.sha256(diff).hexdigest() != W02_PATCH_SHA256:
        raise Blocked("W02_WORKTREE_MISMATCH")
    changed = set(_git(repo, "diff", "--name-only", BASE_COMMIT).decode().splitlines())
    untracked = set(_git(repo, "ls-files", "--others", "--exclude-standard").decode().splitlines())
    if (changed | untracked) - set(W02_FILES + W03_FILES):
        raise Blocked("UNRELATED_PARENT_CHANGES")


def _workspace(parent: Path, destination: Path, patch: Path) -> str:
    _git(parent, "clone", "--quiet", "--local", "--no-hardlinks", "--no-checkout",
         str(parent), str(destination))
    _git(destination, "checkout", "--quiet", "--detach", BASE_COMMIT)
    _git(destination, "apply", "--check", str(patch))
    _git(destination, "apply", str(patch))
    index = destination / ".git" / "launcher-start.index"
    _git(destination, "read-tree", BASE_COMMIT, index=index)
    _git(destination, "add", "--", *W02_FILES, index=index)
    tree = _git(destination, "write-tree", index=index).decode().strip()
    if tree != W02_SOURCE_TREE:
        raise Blocked("W02_SOURCE_TREE_MISMATCH")
    return tree


def _manifest(root: Path, *, git_metadata: bool = False) -> dict[str, tuple[str, int, str]]:
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        if not git_metadata and Path(directory) == root:
            dirs[:] = [name for name in dirs if name != ".git"]
        for name in list(dirs):
            path = Path(directory) / name
            if path.is_symlink():
                files.append(name)
                dirs.remove(name)
        for name in files:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if git_metadata and (relative in {"index", "index.lock"} or
                                 relative.startswith(("objects/", "logs/"))):
                # Ordinary Git reads may refresh the index; commits still alter HEAD/refs.
                continue
            info = path.lstat()
            mode = info.st_mode
            if stat.S_ISREG(mode):
                value = ("file", mode & 0o111, hashlib.sha256(path.read_bytes()).hexdigest())
            elif stat.S_ISLNK(mode):
                value = ("symlink", 0, os.readlink(path))
            else:
                value = ("special", 0, str(mode))
            result[relative] = value
    return result


def _changes(before: dict, after: dict) -> list[str]:
    return sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))


def _usage(ticket: Ticket) -> dict[str, Any]:
    axes = {kind: {"value": None, "unit": None, "status": "UNKNOWN",
                   "reasons": ["OBSERVATIONS_NOT_PROVIDED"]}
            for kind in ("quota", "internal_inference", "cost")}
    result: dict[str, Any] = {"status": "UNKNOWN", "budget_within_limit": "UNKNOWN",
                              "axes": axes, "observations": [], "shots": []}
    if ticket.observations_file is None:
        return result
    if not ticket.observations_file.is_file():
        raise Blocked("OBSERVATIONS_FILE_NOT_FOUND")
    snapshots = load_snapshots(ticket.observations_file)
    summary = summarize_inference(snapshots, launcher=ticket.observation_launcher)
    result["observations"] = [asdict(row) for row in snapshots]
    result["shots"] = [asdict(row) for row in summary.shots]
    result["summary_reasons"] = list(summary.reasons)
    for axis in axes.values():
        axis["reasons"] = ["NO_MATCHING_SHOT_OBSERVATIONS"]
    matches = [shot for shot in summary.shots if shot.shot_id == ticket.task_id]
    if len(matches) != 1:
        return result
    shot = matches[0]
    if shot.metric_kind not in axes:
        for axis in axes.values():
            axis["reasons"] = list(shot.reasons) or ["METRIC_UNKNOWN"]
        return result
    axes[shot.metric_kind] = {
        "value": shot.attributed_usage, "unit": shot.delta_unit, "status": shot.status,
        "reasons": list(shot.reasons), "display_delta": shot.display_delta,
        "display_status": shot.display_status, "display_reasons": list(shot.display_reasons),
    }
    # Per-axis evidence does not make missing axes or the budget known.
    return result


@dataclass(frozen=True)
class CodexCLI:
    executable: str = "codex"
    simulated: bool = False  # test seam; never selected as fallback or by the public CLI

    def path(self) -> str | None:
        return shutil.which(self.executable)

    def argv(self, response: Path, schema: Path) -> list[str]:
        return [self.path() or self.executable, "exec", "--sandbox", "workspace-write", "--json",
                "--output-schema", str(schema), "--output-last-message", str(response), "-"]

    def readiness(self) -> dict[str, Any]:
        found = self.path() is not None
        return {"target": "codex-cli", "mode": "SIMULATED" if self.simulated else "REAL",
                "executable": self.path(), "availability": "FOUND" if found else "BLOCKED",
                "reason": None if found else "CLI_NOT_FOUND", "syntax_source": CLI_DOC,
                "local_help": "UNVERIFIED", "real_ai_connection": "UNVERIFIED"}


class _Lock:
    def __init__(self, path: Path):
        self.path, self.fd = path, None

    def __enter__(self):
        if os.name != "posix":
            raise Blocked("POSIX_PROCESS_CONTROL_REQUIRED")
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self.fd)
            self.fd = None
            if exc.errno in {errno.EAGAIN, errno.EACCES}:
                raise Blocked("ANOTHER_LAUNCH_RUNNING") from exc
            raise
        return self

    def __exit__(self, *args):
        if self.fd is not None:
            os.close(self.fd)  # Never unlink the lock; that could create two lock inodes.


def _kill_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=0.25)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _process(argv: list[str], cwd: Path, deadline: float, log_prefix: Path,
             *, input_text: str | None = None, lock_fd: int | None = None) -> dict[str, Any]:
    started = _now()
    record: dict[str, Any] = {"argv": argv, "cwd": str(cwd), "started_at": started,
                              "exit_code": None, "timed_out": False, "source": "CONTROLLER"}
    if deadline <= time.monotonic():
        record.update(ended_at=_now(), timed_out=True, started=False)
        return record
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    # A caller's PYTHONPATH must not make tests import code from the parent tree.
    env["PYTHONPATH"] = str(cwd / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    record["environment_overrides"] = {key: env[key] for key in ("PYTHONPATH", "PYTHONDONTWRITEBYTECODE")}
    try:
        process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                                   start_new_session=True, close_fds=True,
                                   env=env,
                                   pass_fds=() if lock_fd is None else (lock_fd,))
    except OSError as exc:
        record.update(ended_at=_now(), started=False, reason="PROCESS_START_FAILED", detail=_redact(str(exc)))
        return record
    record.update(pid=process.pid, started=True)
    try:
        stdout, stderr = process.communicate(
            input=None if input_text is None else input_text.encode("utf-8"),
            timeout=max(0.001, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        record["timed_out"] = True
        _kill_group(process)
        stdout, stderr = process.communicate(timeout=2)
    except BaseException:
        _kill_group(process)
        process.wait(timeout=2)
        raise
    finally:
        _kill_group(process)  # Clean surviving children even after a normal leader exit.
    out_path, err_path = log_prefix.with_suffix(".stdout.log"), log_prefix.with_suffix(".stderr.log")
    out_path.write_text(_redact(stdout.decode(errors="replace")), encoding="utf-8")
    err_path.write_text(_redact(stderr.decode(errors="replace")), encoding="utf-8")
    record.update(exit_code=process.returncode, ended_at=_now(), stdout=str(out_path), stderr=str(err_path),
                  local_process_reaped=True, process_group_stop="ATTEMPTED",
                  external_processing_stop="UNKNOWN", external_billing_stop="UNKNOWN")
    return record


def _prompt(ticket: Ticket) -> str:
    data = asdict(ticket)
    data["artifact_dir"] = str(ticket.artifact_dir)
    data["observations_file"] = str(ticket.observations_file) if ticket.observations_file else None
    return json.dumps({
        "ticket": data,
        "controller_contract": (
            "Perform this ticket once in the current isolated repository. Only edit allowed_files. "
            "Do not commit, push, launch other AI, use network services, or retry the task. "
            "Return task_id, status (completed or blocked), and summary using the output schema. "
            "The controller collects your actual diff and independently runs verification_commands; "
            "claims in the final summary are not verification evidence."
        ),
    }, ensure_ascii=False)


def _collect_patch(worker: Path, verifier: Path, output: Path) -> None:
    index = verifier / ".git" / "launcher-result.index"
    _git(verifier, "read-tree", W02_SOURCE_TREE, index=index)
    _git(verifier, "add", "--all", "--force", "--", ".", index=index, work_tree=worker)
    patch = _git(verifier, "diff", "--cached", "--binary", "--full-index", "--no-ext-diff",
                 "--no-textconv", W02_SOURCE_TREE, index=index)
    if not patch.strip():
        raise Blocked("PATCH_MISSING_OR_EMPTY")
    output.write_bytes(patch)
    _git(verifier, "apply", "--check", str(output))
    _git(verifier, "apply", str(output))
    _git(verifier, "diff", "--check", W02_SOURCE_TREE)


def _worker_report(path: Path, task_id: str) -> tuple[dict | None, str | None]:
    if not path.is_file() or path.is_symlink():
        return None, "WORKER_REPORT_MISSING"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "WORKER_REPORT_INVALID"
    if (not isinstance(report, dict) or set(report) != set(REPORT_SCHEMA["required"]) or
            report.get("task_id") != task_id or not isinstance(report.get("status"), str) or
            report["status"] not in {"completed", "blocked"} or
            not isinstance(report.get("summary"), str) or not report["summary"].strip()):
        return None, "WORKER_REPORT_INVALID"
    _write_json(path, report)
    return report, None


def run_ticket(data: dict[str, Any], *, repo: Path, w02_patch: Path,
               execute: bool = False, adapter: CodexCLI | None = None,
               _lock_path: Path | None = None) -> dict[str, Any]:
    """Return a persisted run record (or a read-only preflight/dry-run record).

    Only tests inject simulated=True and a private lock path. Public runs share
    ~/.vector-cannon/launcher.lock regardless of repository or artifact path.
    """
    adapter = adapter or CodexCLI()
    result: dict[str, Any] = {
        "task_id": data.get("task_id") if isinstance(data, dict) else None,
        "state": "NOT_STARTED", "history": [], "target": "codex-cli",
        "simulation": adapter.simulated, "dry_run": not execute, "launch_count": 0,
        "started_at": None, "ended_at": None, "exit_code": None, "stop_reason": None,
        "artifacts": {}, "verification": [], "launch_target": adapter.readiness(),
        "real_connection": (CodexCLI() if adapter.simulated else adapter).readiness(),
        "usage": {"status": "UNKNOWN", "reason": "NOT_OBSERVED"},
        "external_processing_stop": "UNKNOWN", "external_billing_stop": "UNKNOWN",
        "security_boundary": "ISOLATED_DIRECTORY_AND_POST_RUN_CHECK_ONLY",
    }
    run_dir: Path | None = None

    def state(value: str, reason: str | None = None) -> None:
        assert value in STATES
        result.update(state=value, stop_reason=reason)
        result["history"].append({"state": value, "at": _now(), "reason": reason})
        if value == "RUNNING":
            result["started_at"] = _now()
        elif value != "NOT_STARTED":
            result["ended_at"] = _now()
        if run_dir is not None:
            _write_json(run_dir / "result.json", result)

    try:
        ticket = Ticket.from_dict(data)
        repo, w02_patch = repo.resolve(), w02_patch.resolve()
        if ticket.artifact_dir == repo or repo in ticket.artifact_dir.parents:
            raise Blocked("ARTIFACT_DIR_INSIDE_PARENT_REPOSITORY")
        _check_source(repo, w02_patch)
        result["baseline"] = {"commit": BASE_COMMIT, "w02_patch_sha256": W02_PATCH_SHA256,
                               "source_tree": W02_SOURCE_TREE}
        result["usage"] = _usage(ticket)
        prospective = ticket.artifact_dir / ticket.task_id
        response, schema = prospective / "worker-report.json", prospective / "response-schema.json"
        result["plan"] = {"argv": adapter.argv(response, schema), "cwd": str(prospective / "workspace"),
                          "input": "ticket JSON via stdin", "result": str(response),
                          "verification_commands": ticket.verification_commands,
                          "timeout_seconds": ticket.timeout_seconds, "maximum_launches": 1}
        if adapter.path() is None:
            raise Blocked("CLI_NOT_FOUND")
        if not execute:
            state("NOT_STARTED", "DRY_RUN")
            return _safe_json(result)
        with _Lock(_lock_path or Path.home() / ".vector-cannon" / "launcher.lock") as lock:
            prospective.mkdir(parents=True, exist_ok=False, mode=0o700)
            run_dir = prospective
            result["artifacts"]["result"] = str(run_dir / "result.json")
            state("NOT_STARTED", "PREFLIGHT")
            # Help does not execute an AI task. Its evidence is distinct from connectivity.
            help_result = _process([adapter.path(), "exec", "--help"], repo, time.monotonic() + 5,
                                   run_dir / "cli-help", lock_fd=lock.fd)
            result["cli_help"] = help_result
            help_text = "".join(Path(help_result[key]).read_text() for key in ("stdout", "stderr") if key in help_result)
            if help_result["exit_code"] != 0 or help_result["timed_out"] or any(
                flag not in help_text for flag in ("--sandbox", "--json", "--output-schema", "--output-last-message")
            ):
                raise Blocked("CLI_SYNTAX_UNVERIFIED")
            result["launch_target"]["local_help"] = "SIMULATED" if adapter.simulated else "VERIFIED"
            if not adapter.simulated:
                result["real_connection"]["local_help"] = "VERIFIED"
            _write_json(schema, REPORT_SCHEMA)
            worker, verifier = run_dir / "workspace", run_dir / "verification-workspace"
            _workspace(repo, worker, w02_patch)
            _workspace(repo, verifier, w02_patch)
            before = _manifest(worker)
            git_before = _manifest(worker / ".git", git_metadata=True)
            verification_before = _manifest(verifier)
            verification_git_before = _manifest(verifier / ".git", git_metadata=True)
            _check_source(repo, w02_patch)  # Detect a source change during workspace preparation.
            deadline = time.monotonic() + ticket.timeout_seconds
            result["launch_count"] = 1
            state("RUNNING")
            result["execution"] = _process(adapter.argv(response, schema), worker, deadline,
                                            run_dir / "worker", input_text=_prompt(ticket), lock_fd=lock.fd)
            execution = result["execution"]
            result["launch_count"] = int(execution["started"])
            result["exit_code"] = execution["exit_code"]
            result["artifacts"].update(workspace=str(worker), stdout=execution.get("stdout"),
                                         stderr=execution.get("stderr"))
            after = _manifest(worker)
            changed = _changes(before, after)
            result["changed_files"] = changed
            violations = sorted(set(changed) - set(ticket.allowed_files))
            violations += [name for name in changed if name in after and after[name][0] != "file"]
            if git_before != _manifest(worker / ".git", git_metadata=True):
                violations.append(".git")
            if (verification_before != _manifest(verifier) or
                    verification_git_before != _manifest(verifier / ".git", git_metadata=True)):
                violations.append("verification-workspace")
            result["scope_violations"] = sorted(set(violations))
            report, report_reason = _worker_report(response, ticket.task_id)
            result["worker_report_reason"] = report_reason
            if report is not None:
                result["worker_claim"] = report
                result["artifacts"]["worker_report"] = str(response)
            patch_path = run_dir / "changes.patch"
            # Retain an inspectable partial patch even when the worker fails, when scope permits.
            if not violations:
                try:
                    _collect_patch(worker, verifier, patch_path)
                    result["artifacts"]["patch"] = str(patch_path)
                    result["patch_apply_check"] = "PASSED"
                except Blocked as exc:
                    result["patch_apply_check"] = "FAILED"
                    result["patch_reason"] = str(exc)
            if execution["timed_out"]:
                state("TIMED_OUT", "EXECUTION_TIME_LIMIT")
            elif not execution["started"]:
                state("BLOCKED", "PROCESS_START_FAILED")
            elif execution["exit_code"] != 0:
                state("FAILED", "NONZERO_EXIT")
            elif violations:
                state("FAILED", "CHANGES_OUTSIDE_ALLOWED_FILES")
            elif report is not None and report["status"] == "blocked":
                state("BLOCKED", "WORKER_REPORTED_BLOCKED")
            elif result.get("patch_apply_check") != "PASSED":
                state("FAILED", result.get("patch_reason", "PATCH_UNVERIFIED"))
            elif report_reason:
                state("FAILED", report_reason)
            else:
                for number, argv in enumerate(ticket.verification_commands):
                    record = _process(list(argv), verifier, deadline, run_dir / f"verify-{number}", lock_fd=lock.fd)
                    result["verification"].append(record)
                    _write_json(run_dir / "verification.json", result["verification"])
                    result["artifacts"]["verification"] = str(run_dir / "verification.json")
                    if record["timed_out"] or record["exit_code"] != 0:
                        break
                if any(item["timed_out"] for item in result["verification"]):
                    state("TIMED_OUT", "VERIFICATION_TIME_LIMIT")
                elif (len(result["verification"]) != len(ticket.verification_commands) or
                      any(item["exit_code"] != 0 for item in result["verification"])):
                    state("FAILED", "VERIFICATION_FAILED")
                else:
                    state("SUCCEEDED", "PATCH_AND_CONTROLLER_VERIFICATION_PASSED")
    except (Blocked, OSError, ValueError, subprocess.SubprocessError) as exc:
        # A missing artifact after execution is a failure, not a successful empty run.
        state("FAILED" if result["launch_count"] else "BLOCKED", str(exc))
    except KeyboardInterrupt:
        state("FAILED" if result["launch_count"] else "BLOCKED", "INTERRUPTED")
    return _safe_json(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticket", required=True, type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--w02-patch", required=True, type=Path)
    parser.add_argument("--codex-path", default="codex")
    parser.add_argument("--execute", action="store_true", help="Explicitly launch once; default is dry-run")
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.ticket.read_text(encoding="utf-8"))
        result = run_ticket(data, repo=args.repo, w02_patch=args.w02_patch,
                            execute=args.execute, adapter=CodexCLI(args.codex_path))
    except (OSError, ValueError) as exc:
        result = {"state": "BLOCKED", "stop_reason": str(exc), "launch_count": 0}
    print(json.dumps(_safe_json(result), ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if result["state"] in {"NOT_STARTED", "SUCCEEDED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
