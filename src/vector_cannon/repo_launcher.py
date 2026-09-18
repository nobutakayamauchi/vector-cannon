"""Generic isolated repository launcher for adaptive Vector Cannon missions.

Unlike launcher.py (the pinned W02/W03 acceptance launcher), this module accepts
an arbitrary clean Git repository commit. The source repository is never used as
an AI working directory. A detached local clone is created for the worker and a
second clone is used for controller verification.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .launcher import (
    Blocked,
    CodexCLI,
    REPORT_SCHEMA,
    STATES,
    _Lock,
    _changes,
    _git,
    _manifest,
    _now,
    _process,
    _redact,
    _relative_file,
    _safe_json,
    _worker_report,
    _write_json,
)


_COMMIT = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class RepoTicket:
    task_id: str
    instructions: str
    base_commit: str
    allowed_files: tuple[str, ...]
    verification_commands: tuple[tuple[str, ...], ...]
    timeout_seconds: float
    artifact_dir: Path
    target: str = "codex-cli"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RepoTicket":
        if not isinstance(data, dict):
            raise Blocked("INVALID_TICKET")
        required = {
            "task_id", "instructions", "base_commit", "allowed_files",
            "verification_commands", "timeout_seconds", "artifact_dir",
        }
        missing = required - data.keys()
        if missing:
            raise Blocked("MISSING_FIELDS:" + ",".join(sorted(missing)))
        if data.get("target", "codex-cli") != "codex-cli":
            raise Blocked("INVALID_TARGET")
        task_id = data["task_id"]
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", task_id):
            raise Blocked("INVALID_TASK_ID")
        instructions = data["instructions"]
        if not isinstance(instructions, str) or not instructions.strip():
            raise Blocked("EMPTY_INSTRUCTIONS")
        base = data["base_commit"]
        if not isinstance(base, str) or not _COMMIT.fullmatch(base):
            raise Blocked("INVALID_BASE_COMMIT")
        allowed = data["allowed_files"]
        if not isinstance(allowed, list) or not allowed:
            raise Blocked("MISSING_ALLOWED_FILES")
        allowed_files = tuple(_relative_file(item) for item in allowed)
        if len(set(allowed_files)) != len(allowed_files):
            raise Blocked("DUPLICATE_ALLOWED_FILES")
        commands = data["verification_commands"]
        if not isinstance(commands, list) or not commands:
            raise Blocked("MISSING_VERIFICATION_COMMANDS")
        normalized_commands: list[tuple[str, ...]] = []
        for argv in commands:
            if (
                not isinstance(argv, list)
                or not argv
                or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv)
            ):
                raise Blocked("VERIFICATION_REQUIRES_ARGV_ARRAYS")
            normalized_commands.append(tuple(argv))
        timeout = data["timeout_seconds"]
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise Blocked("INVALID_TIMEOUT")
        output = data["artifact_dir"]
        if not isinstance(output, str) or not Path(output).is_absolute():
            raise Blocked("ARTIFACT_DIR_MUST_BE_ABSOLUTE")
        return cls(
            task_id=task_id,
            instructions=instructions.strip(),
            base_commit=base,
            allowed_files=allowed_files,
            verification_commands=tuple(normalized_commands),
            timeout_seconds=float(timeout),
            artifact_dir=Path(output).resolve(),
        )


def repository_identity(repo: Path) -> str:
    repo = repo.expanduser().resolve()
    if not repo.is_dir():
        raise Blocked("REPOSITORY_NOT_FOUND")
    head = _git(repo, "rev-parse", "HEAD").decode().strip()
    if not _COMMIT.fullmatch(head):
        raise Blocked("INVALID_REPOSITORY_HEAD")
    status = _git(repo, "status", "--porcelain=v1", "--untracked-files=all").decode()
    if status.strip():
        raise Blocked("SOURCE_REPOSITORY_NOT_CLEAN")
    return head


def _check_source(repo: Path, base_commit: str) -> None:
    if repository_identity(repo) != base_commit:
        raise Blocked("BASE_COMMIT_MISMATCH")


def _clone_at(source: Path, destination: Path, base_commit: str) -> None:
    if destination.exists():
        raise Blocked("WORKSPACE_ALREADY_EXISTS")
    destination.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        "git",
        "-c", "core.hooksPath=/dev/null",
        "-c", "core.fsmonitor=false",
        "clone", "--quiet", "--local", "--no-hardlinks", "--no-checkout",
        str(source), str(destination),
    ]
    completed = subprocess.run(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    if completed.returncode:
        raise Blocked("GIT_CLONE_ERROR:" + _redact(completed.stderr.decode(errors="replace").strip()))
    _git(destination, "checkout", "--quiet", "--detach", base_commit)
    if _git(destination, "status", "--porcelain=v1", "--untracked-files=all").decode().strip():
        raise Blocked("CLONED_WORKSPACE_NOT_CLEAN")


def _collect_patch(
    *,
    worker: Path,
    verifier: Path,
    base_commit: str,
    output: Path,
) -> None:
    base_tree = _git(verifier, "rev-parse", f"{base_commit}^{{tree}}").decode().strip()
    index = verifier / ".git" / "repo-launcher-result.index"
    _git(verifier, "read-tree", base_tree, index=index)
    _git(verifier, "add", "--all", "--force", "--", ".", index=index, work_tree=worker)
    patch = _git(
        verifier,
        "diff", "--cached", "--binary", "--full-index", "--no-ext-diff",
        "--no-textconv", base_tree,
        index=index,
    )
    if not patch.strip():
        raise Blocked("PATCH_MISSING_OR_EMPTY")
    output.write_bytes(patch)
    _git(verifier, "apply", "--check", str(output))
    _git(verifier, "apply", str(output))
    _git(verifier, "diff", "--check", base_commit)


def _prompt(ticket: RepoTicket) -> str:
    data = asdict(ticket)
    data["artifact_dir"] = str(ticket.artifact_dir)
    return json.dumps(
        {
            "ticket": data,
            "controller_contract": (
                "Perform this ticket once in the current isolated repository. "
                "Only edit allowed_files. Do not commit, push, launch other AI, "
                "use network services, or retry the task. Return task_id, status "
                "(completed or blocked), and summary using the output schema. "
                "The controller collects the actual diff and independently runs "
                "verification_commands; your summary is not verification evidence."
            ),
        },
        ensure_ascii=False,
    )


def run_repo_ticket(
    data: dict[str, Any],
    *,
    repo: Path,
    execute: bool = False,
    adapter: CodexCLI | None = None,
    _lock_path: Path | None = None,
) -> dict[str, Any]:
    adapter = adapter or CodexCLI()
    result: dict[str, Any] = {
        "task_id": data.get("task_id") if isinstance(data, dict) else None,
        "state": "NOT_STARTED",
        "history": [],
        "target": "codex-cli",
        "simulation": adapter.simulated,
        "dry_run": not execute,
        "launch_count": 0,
        "started_at": None,
        "ended_at": None,
        "exit_code": None,
        "stop_reason": None,
        "artifacts": {},
        "verification": [],
        "launch_target": adapter.readiness(),
        "real_connection": (CodexCLI() if adapter.simulated else adapter).readiness(),
        "usage": {"status": "UNKNOWN", "reason": "NOT_OBSERVED"},
        "external_processing_stop": "UNKNOWN",
        "external_billing_stop": "UNKNOWN",
        "security_boundary": "ISOLATED_CLONE_ALLOWED_FILE_SCOPE_AND_CONTROLLER_VERIFICATION",
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
        ticket = RepoTicket.from_dict(data)
        repo = repo.expanduser().resolve()
        if ticket.artifact_dir == repo or repo in ticket.artifact_dir.parents:
            raise Blocked("ARTIFACT_DIR_INSIDE_SOURCE_REPOSITORY")
        _check_source(repo, ticket.base_commit)
        source_tree = _git(repo, "rev-parse", f"{ticket.base_commit}^{{tree}}").decode().strip()
        result["baseline"] = {
            "commit": ticket.base_commit,
            "source_tree": source_tree,
        }
        prospective = ticket.artifact_dir / ticket.task_id
        response = prospective / "worker-report.json"
        schema = prospective / "response-schema.json"
        result["plan"] = {
            "argv": adapter.argv(response, schema),
            "cwd": str(prospective / "workspace"),
            "input": "ticket JSON via stdin",
            "result": str(response),
            "verification_commands": ticket.verification_commands,
            "timeout_seconds": ticket.timeout_seconds,
            "maximum_launches": 1,
        }
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

            help_result = _process(
                adapter.help_argv(),
                repo,
                time.monotonic() + 5,
                run_dir / "cli-help",
                lock_fd=lock.fd,
            )
            result["cli_help"] = help_result
            help_text = "".join(
                Path(help_result[key]).read_text()
                for key in ("stdout", "stderr")
                if key in help_result
            )
            if (
                help_result["exit_code"] != 0
                or help_result["timed_out"]
                or any(
                    flag not in help_text
                    for flag in ("--sandbox", "--json", "--output-schema", "--output-last-message")
                )
            ):
                raise Blocked("CLI_SYNTAX_UNVERIFIED")
            result["launch_target"]["local_help"] = "SIMULATED" if adapter.simulated else "VERIFIED"
            if not adapter.simulated:
                result["real_connection"]["local_help"] = "VERIFIED"

            _write_json(schema, REPORT_SCHEMA)
            worker = run_dir / "workspace"
            verifier = run_dir / "verification-workspace"
            _clone_at(repo, worker, ticket.base_commit)
            _clone_at(repo, verifier, ticket.base_commit)

            before = _manifest(worker)
            git_before = _manifest(worker / ".git", git_metadata=True)
            verification_before = _manifest(verifier)
            verification_git_before = _manifest(verifier / ".git", git_metadata=True)
            _check_source(repo, ticket.base_commit)

            deadline = time.monotonic() + ticket.timeout_seconds
            result["launch_count"] = 1
            state("RUNNING")
            result["execution"] = _process(
                adapter.argv(response, schema),
                worker,
                deadline,
                run_dir / "worker",
                input_text=_prompt(ticket),
                lock_fd=lock.fd,
            )
            execution = result["execution"]
            result["launch_count"] = int(execution["started"])
            result["exit_code"] = execution["exit_code"]
            result["artifacts"].update(
                workspace=str(worker),
                stdout=execution.get("stdout"),
                stderr=execution.get("stderr"),
            )

            after = _manifest(worker)
            changed = _changes(before, after)
            result["changed_files"] = changed
            violations = sorted(set(changed) - set(ticket.allowed_files))
            violations += [
                name
                for name in changed
                if name in after and after[name][0] != "file"
            ]
            if git_before != _manifest(worker / ".git", git_metadata=True):
                violations.append(".git")
            if (
                verification_before != _manifest(verifier)
                or verification_git_before != _manifest(verifier / ".git", git_metadata=True)
            ):
                violations.append("verification-workspace")
            result["scope_violations"] = sorted(set(violations))

            report, report_reason = _worker_report(response, ticket.task_id)
            result["worker_report_reason"] = report_reason
            if report is not None:
                result["worker_claim"] = report
                result["artifacts"]["worker_report"] = str(response)

            patch_path = run_dir / "changes.patch"
            if not violations:
                try:
                    _collect_patch(
                        worker=worker,
                        verifier=verifier,
                        base_commit=ticket.base_commit,
                        output=patch_path,
                    )
                    result["artifacts"]["patch"] = str(patch_path)
                    result["patch_apply_check"] = "PASSED"
                    result["patch_sha256"] = hashlib.sha256(patch_path.read_bytes()).hexdigest()
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
                    record = _process(
                        list(argv),
                        verifier,
                        deadline,
                        run_dir / f"verify-{number}",
                        lock_fd=lock.fd,
                    )
                    result["verification"].append(record)
                    _write_json(run_dir / "verification.json", result["verification"])
                    result["artifacts"]["verification"] = str(run_dir / "verification.json")
                    if record["timed_out"] or record["exit_code"] != 0:
                        break
                if any(item["timed_out"] for item in result["verification"]):
                    state("TIMED_OUT", "VERIFICATION_TIME_LIMIT")
                elif (
                    len(result["verification"]) != len(ticket.verification_commands)
                    or any(item["exit_code"] != 0 for item in result["verification"])
                ):
                    state("FAILED", "VERIFICATION_FAILED")
                else:
                    _check_source(repo, ticket.base_commit)
                    state("SUCCEEDED", "PATCH_AND_CONTROLLER_VERIFICATION_PASSED")
    except (Blocked, OSError, ValueError, subprocess.SubprocessError) as exc:
        state("FAILED" if result["launch_count"] else "BLOCKED", str(exc))
    except KeyboardInterrupt:
        state("FAILED" if result["launch_count"] else "BLOCKED", "INTERRUPTED")
    return _safe_json(result)
