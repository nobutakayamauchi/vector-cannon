"""Mission -> isolated candidate -> independent evidence -> bounded decision -> delivery."""
from __future__ import annotations

import hashlib
import os
import subprocess
import time
from pathlib import Path

from .adapters import Agent, Judge
from .model import Contract, ControlError, canonical, changed, copy_snapshot, digest, manifest, money
from .store import Store, TERMINAL
from .supervisor import build_dot_handoff
from .verify import verify


def git(root: Path, *args: str) -> bytes:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    cmd = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
           "-c", "core.autocrlf=false", "-C", str(root), *args]
    result = subprocess.run(cmd, capture_output=True, env=env, timeout=30)
    if result.returncode:
        raise ControlError("LOCAL_GIT_OPERATION_FAILED")
    return result.stdout


def init_git(root: Path):
    git(root, "init", "-q")
    git(root, "add", "--all", "--force")
    git(root, "-c", "user.name=Vector Cannon", "-c", "user.email=controller@localhost",
        "-c", "commit.gpgsign=false", "commit", "-qm", "Controller-owned snapshot", "--allow-empty")


def git_metadata(root: Path) -> dict:
    result = {}
    gitdir = root / ".git"
    if not gitdir.is_dir() or gitdir.is_symlink():
        raise ControlError("WORKER_GIT_METADATA_INVALID")
    for path in gitdir.rglob("*"):
        if path.is_symlink():
            raise ControlError("WORKER_GIT_METADATA_SYMLINK")
        if path.is_file() and path.name not in {"index", "index.lock"}:
            result[path.relative_to(gitdir).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def source_identity(source: Path) -> dict:
    head = None
    if (source / ".git").exists():
        head = git(source, "rev-parse", "HEAD").decode().strip()
    return {"source": str(source), "source_hash": digest(manifest(source, source_input=True)), "git_head": head}


class OutcomeEngine:
    def __init__(self, store: Store, agent: Agent, judges: dict[str, Judge] | None = None):
        self.store, self.agent, self.judges = store, agent, judges or {}
        if set(self.judges) - {"jev", "sol", "astra"}:
            raise ControlError("UNKNOWN_DECISION_ROLE")

    @classmethod
    def create(cls, contract: Contract, source: Path, directory: Path, agent: Agent,
               judges: dict[str, Judge] | None = None):
        source, directory = source.expanduser().resolve(), directory.expanduser().resolve()
        if source == directory or source in directory.parents:
            raise ControlError("SESSION_MUST_BE_OUTSIDE_SOURCE")
        identity = source_identity(source)
        identity["agent"] = agent.identity()
        identity["judges"] = {role: judge.identity() for role, judge in (judges or {}).items()}
        store = Store.create(directory, contract, identity)
        copy_snapshot(source, directory / "baseline", source_input=True)
        state = store.load()
        state.update(phase="VERIFY", reason="BASELINE_READY")
        store.save(state, "BASELINE_CREATED")
        return cls(store, agent, judges)

    def _snapshot(self, state: dict) -> Path:
        # The state database is controller-owned; paths never come from an agent or judge.
        path = self.store.directory / state["snapshot"]
        if path.is_symlink() or path.resolve().parent != self.store.directory:
            raise ControlError("SNAPSHOT_PATH_INVALID")
        if digest(manifest(path)) != state["snapshot_hash"]:
            raise ControlError("SNAPSHOT_CHANGED_OUTSIDE_CONTROLLER")
        return path

    def _identity(self, state: dict):
        expected = state["identity"]
        actual = source_identity(Path(expected["source"]))
        if any(actual[k] != expected[k] for k in actual):
            raise ControlError("SOURCE_IDENTITY_CHANGED")
        if self.agent.identity() != expected["agent"]:
            raise ControlError("AGENT_IDENTITY_CHANGED")
        if {r: j.identity() for r, j in self.judges.items()} != expected["judges"]:
            raise ControlError("JUDGE_CONFIGURATION_CHANGED")
        self._snapshot(state)

    def _stop(self, state: dict, phase: str, reason: str):
        state.update(phase=phase, reason=reason)
        self.store.save(state, reason)
        return self.store.report()

    def _dot_handoff(self, state: dict, contract: Contract, reason: str):
        if contract.payload.get("supervisor") != "dot":
            state.update(phase="HUMAN_REQUIRED", reason=reason)
            self.store.save(state, "HUMAN_SUPERVISOR_REQUIRED")
            return
        if contract.payload["data_policy"] != "redacted_evidence":
            self._stop(state, "HUMAN_REQUIRED", "EXTERNAL_EVIDENCE_NOT_AUTHORIZED")
            return
        if state["phase"] != "HANDOFF_PENDING":
            state.update(phase="HANDOFF_PENDING", reason=reason, handoff_reason=reason)
            self.store.save(state, "DOT_HANDOFF_PENDING")
        handoff = build_dot_handoff(self.store, state, contract, reason=reason)
        state.pop("handoff_reason", None)
        state["handoff"] = handoff
        state.update(phase="DOT_HANDOFF", reason=reason)
        self.store.save(state, "DOT_HANDOFF_CREATED")

    def _role_available(self, state: dict, contract: Contract, role: str) -> bool:
        return (
            role in self.judges
            and state["calls"][role] < contract.limits["judge_calls"][role]
        )

    def _verify(self, state: dict, contract: Contract):
        snapshot = self._snapshot(state)
        root = self.store.directory / f"verification-{state['generation']:04d}"
        copy_snapshot(snapshot, root)
        runner = getattr(self.agent, "verify_command", None)
        evidence = verify(contract, root, revision=state["candidate_revision"], command=runner,
                          timeout=max(0.01, state["deadline"] - time.time()))
        # Check the original immutable candidate, not the verifier's temporary cache.
        self._snapshot(state)
        if evidence["snapshot_hash"] != state["snapshot_hash"]:
            raise ControlError("EVIDENCE_REVISION_MISMATCH")
        state["evidence"] = evidence
        statuses = [c["status"] for c in evidence["checks"]]
        if "UNKNOWN" in statuses:
            state.update(phase="HUMAN_REQUIRED", reason="VERIFICATION_UNKNOWN")
        elif all(s == "PASS" for s in statuses):
            if contract.payload["human_final"]:
                state.update(phase="HUMAN_REQUIRED", reason="FINAL_ACCEPTANCE_REQUIRED")
            elif contract.payload["semantic_review"]:
                state.update(phase="JUDGE", next_role="jev", reason="SEMANTIC_REVIEW_REQUIRED")
            else:
                state.update(phase="SEAL", reason="ALL_DECLARED_CHECKS_PASSED")
        elif state["no_progress"] >= contract.limits["no_progress"]:
            state.update(phase="HUMAN_REQUIRED", reason="NO_PROGRESS")
        elif state["calls"]["worker"] >= contract.limits["shots"]:
            state.update(phase="LIMIT_REACHED", reason="WORKER_CALL_LIMIT")
        else:
            # Test failures are deterministic repair signals. Do not spend Jev/Astra here.
            state.update(phase="READY", reason="DETERMINISTIC_REPAIR_REQUIRED")
        self.store.save(state, "CONTROLLER_VERIFICATION")

    def _worker(self, state: dict, contract: Contract, fd: int):
        snapshot = self._snapshot(state)
        workspace = self.store.directory / f"work-{state['calls']['worker'] + 1:03d}"
        before = copy_snapshot(snapshot, workspace)
        init_git(workspace)
        metadata = git_metadata(workspace)
        self.agent.preflight(workspace)  # No inference; do not 'repair' a broken sandbox with a stronger AI.
        operation_id = self.store.reserve(state, "worker")
        receipt = self.agent.execute(contract, workspace, self.store.directory / (operation_id + "-logs"),
                                     {"operation_id": operation_id,
                                      "checks": (state["evidence"] or {}).get("checks", []),
                                      "supervisor_note": state.get("supervisor_note")},
                                     max(0.01, state["deadline"] - time.time()), fd)
        if not isinstance(receipt, dict):
            raise ControlError("WORKER_RECEIPT_INVALID")
        self.store.finish(state, receipt, "CANDIDATE_RECEIVED")
        if state["phase"] in TERMINAL:
            return
        after = manifest(workspace)
        differences = changed(before, after)
        violation = set(differences) - set(contract.payload["allowed_files"])
        if git_metadata(workspace) != metadata:
            violation.add(".git")
        state["last_changed_files"] = differences
        execution = receipt.get("execution", {})
        execution_failed = bool(execution) and (execution.get("exit_code") != 0 or execution.get("timed_out")
                                                or execution.get("output_limited"))
        claim = receipt.get("worker_claim") or {}
        worker_blocked = isinstance(claim, dict) and claim.get("status") == "blocked"
        if violation:
            self._stop(state, "BLOCKED", "SCOPE_VIOLATION:" + ",".join(sorted(violation)))
            return
        if execution_failed or worker_blocked:
            self._stop(state, "BLOCKED", "WORKER_BLOCKED_OR_EXECUTION_FAILED")
            return
        # Retain scoped but not-yet-correct candidates for repair. They are NOT accepted outcomes.
        state["no_progress"] = state["no_progress"] + 1 if after == before else 0
        state["snapshot"], state["snapshot_hash"] = workspace.name, digest(after)
        state["candidate_revision"] += 1
        state["supervisor_note"] = None
        state["handoff"] = None
        state["supervisor_decision"] = None
        state.update(phase="VERIFY", reason="CANDIDATE_NOT_YET_VERIFIED")
        self.store.save(state, "CANDIDATE_ADOPTED_FOR_VERIFICATION")

    def _judge(self, state: dict, contract: Contract):
        role = state["next_role"]
        if role in self.judges and state["calls"][role] >= contract.limits["judge_calls"][role]:
            state.update(phase="LIMIT_REACHED", reason=f"{role.upper()}_CALL_LIMIT")
            self.store.save(state, "DECISION_CALL_LIMIT")
            return
        if contract.payload["data_policy"] != "redacted_evidence":
            self._stop(state, "HUMAN_REQUIRED", "EXTERNAL_EVIDENCE_NOT_AUTHORIZED")
            return
        if not self._role_available(state, contract, role):
            if role == "jev" and self._role_available(state, contract, "sol"):
                state.update(phase="JUDGE", next_role="sol", reason="JEV_UNAVAILABLE_ESCALATED")
                self.store.save(state, "DECISION_TIER_SKIPPED")
            elif contract.payload.get("supervisor") == "dot":
                self._dot_handoff(state, contract, "DECISION_TIER_UNAVAILABLE:" + role)
            elif role != "astra" and self._role_available(state, contract, "astra"):
                state.update(phase="JUDGE", next_role="astra", reason="LEGACY_ASTRA_FALLBACK")
                self.store.save(state, "DECISION_TIER_SKIPPED")
            else:
                self._stop(state, "HUMAN_REQUIRED", "DECISION_ROLE_NOT_CONFIGURED:" + role)
            return
        self._snapshot(state)
        evidence = state["evidence"]
        if evidence["contract_hash"] != contract.hash or evidence["snapshot_hash"] != state["snapshot_hash"]:
            raise ControlError("STALE_EVIDENCE")
        self.store.reserve(state, role)
        receipt = self.judges[role].decide(contract, evidence, max(0.01, state["deadline"] - time.time()))
        if not isinstance(receipt, dict):
            raise ControlError("INVALID_DECISION_RECEIPT")
        self.store.finish(state, receipt, "DECISION_RECEIVED")
        if state["phase"] in TERMINAL:
            return
        self._snapshot(state)
        valid = (receipt.get("valid") is True and receipt.get("evidence_hash") == evidence["hash"]
                 and receipt.get("role") == role and type(receipt.get("confidence")) in {int, float}
                 and 0.9 <= receipt["confidence"] <= 1)
        state["decisions"].append({"role": role, "choice": receipt.get("choice"), "valid": valid,
                                   "confidence": receipt.get("confidence"),
                                   "confidence_kind": receipt.get("confidence_kind"),
                                   "evidence_hash": receipt.get("evidence_hash"), "model": receipt.get("model")})
        choice = receipt.get("choice") if valid else "ESCALATE"
        if choice == "ACCEPT":
            if not all(c["status"] == "PASS" for c in evidence["checks"]):
                raise ControlError("JUDGE_CANNOT_OVERRIDE_FAILED_CHECKS")
            state.update(phase="SEAL", reason="SEMANTIC_REVIEW_ACCEPTED")
        elif choice == "REPAIR":
            if state["calls"]["worker"] >= contract.limits["shots"]:
                state.update(phase="LIMIT_REACHED", reason="WORKER_CALL_LIMIT")
            elif state["no_progress"] >= contract.limits["no_progress"]:
                state.update(phase="HUMAN_REQUIRED", reason="NO_PROGRESS")
            else:
                state.update(phase="READY", reason="SEMANTIC_REPAIR_REQUESTED")
        elif choice == "HUMAN_REQUIRED":
            state.update(phase="HUMAN_REQUIRED", reason="JUDGE_REQUESTED_HUMAN")
        else:
            if role == "jev" and self._role_available(state, contract, "sol"):
                state.update(phase="JUDGE", next_role="sol", reason="ESCALATION_REQUIRED")
            elif contract.payload.get("supervisor") == "dot":
                self.store.save(state, "DECISION_VALIDATED")
                self._dot_handoff(state, contract, "DOT_SUPERVISOR_REVIEW_REQUIRED")
                return
            elif role != "astra" and "astra" in self.judges:
                if self._role_available(state, contract, "astra"):
                    state.update(phase="JUDGE", next_role="astra", reason="LEGACY_ASTRA_ESCALATION")
                else:
                    state.update(phase="LIMIT_REACHED", reason="ASTRA_CALL_LIMIT")
            else:
                state.update(phase="HUMAN_REQUIRED", reason="AUTOMATED_REVIEW_EXHAUSTED")
        self.store.save(state, "DECISION_VALIDATED")

    def _seal(self, state: dict, contract: Contract):
        self._identity(state)
        evidence = state["evidence"] or {}
        if contract.payload["human_final"] and state.get("human_acceptance", {}).get("evidence_hash") != evidence.get("hash"):
            raise ControlError("FINAL_ACCEPTANCE_REQUIRED")
        checks = evidence.get("checks", [])
        expected = {c["id"] for c in contract.payload["criteria"]}
        if (evidence.get("contract_hash") != contract.hash or evidence.get("snapshot_hash") != state["snapshot_hash"]
                or {c["id"] for c in checks} != expected or len(checks) != len(expected)
                or not all(c["status"] == "PASS" and c["producer"] == "CONTROLLER" for c in checks)):
            raise ControlError("INCOMPLETE_COMPLETION_EVIDENCE")
        snapshot = self._snapshot(state)
        delivery = self.store.directory / "delivery"
        if delivery.exists():
            if digest(manifest(delivery)) != state["snapshot_hash"]:
                raise ControlError("DELIVERY_IDENTITY_MISMATCH")
        else:
            copy_snapshot(snapshot, delivery)
        # Synthetic Git baseline supports ordinary artifact directories as well as coding repos.
        export = self.store.directory / f"export-{state['generation']:04d}"
        original = copy_snapshot(self.store.directory / "baseline", export)
        init_git(export)
        final = manifest(snapshot)
        for name in set(original) - set(final):
            (export / name).unlink()
        for name, info in final.items():
            target = export / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((snapshot / name).read_bytes())
            target.chmod(0o700 if info["executable"] else 0o600)
        git(export, "add", "--all", "--force")
        patch = git(export, "diff", "--cached", "--binary", "--full-index", "--no-ext-diff", "--no-textconv", "HEAD")
        patch_path = self.store.directory / "final.patch"
        patch_path.write_bytes(patch)
        state["output"] = {"directory": str(delivery), "snapshot_hash": state["snapshot_hash"],
                           "patch": str(patch_path), "patch_sha256": hashlib.sha256(patch).hexdigest(),
                           "changed_files": changed(original, final)}
        state.update(phase="VERIFIED_DONE", reason="DECLARED_ACCEPTANCE_VERIFIED_NOT_DEPLOYED")
        self.store.save(state, "OUTCOME_SEALED")

    def run(self, *, max_operations: int | None = None) -> dict:
        with self.store.lock() as fd:
            state = self.store.load()
            if state["in_flight"] is not None:
                return self._stop(state, "HUMAN_REQUIRED", "UNCERTAIN_EXTERNAL_OPERATION_DO_NOT_REPLAY")
            if state["phase"] in TERMINAL:
                return self.store.report()
            contract = Contract.parse(state["contract"])
            operations = 0
            try:
                self._identity(state)
                while state["phase"] not in TERMINAL:
                    if time.time() >= state["deadline"]:
                        return self._stop(state, "LIMIT_REACHED", "MISSION_DEADLINE")
                    if max_operations is not None and operations >= max_operations:
                        return self.store.report()  # Durable checkpoint, not an active background task.
                    if state["phase"] == "VERIFY":
                        self._verify(state, contract)
                    elif state["phase"] == "READY":
                        self._worker(state, contract, fd)
                        operations += 1
                    elif state["phase"] == "JUDGE":
                        self._judge(state, contract)
                        operations += 1
                    elif state["phase"] == "HANDOFF_PENDING":
                        self._dot_handoff(state, contract, state["handoff_reason"])
                    elif state["phase"] == "SEAL":
                        self._seal(state, contract)
                    else:
                        return self._stop(state, "HUMAN_REQUIRED", "INTERRUPTED_CHECKPOINT_REQUIRES_RECONCILIATION")
            except (ControlError, OSError, ValueError, subprocess.SubprocessError) as exc:
                if isinstance(exc, OSError) and state["phase"] == "HANDOFF_PENDING":
                    # Only local publication is retried; the durable checkpoint skips judges/workers.
                    return self._stop(state, "HANDOFF_PENDING", "DOT_HANDOFF_IO_RETRY_REQUIRED")
                # A timeout or broken connection can still have billed/executed remotely.
                reason = str(exc) if isinstance(exc, ControlError) else type(exc).__name__
                phase = "HUMAN_REQUIRED" if state["in_flight"] else "BLOCKED"
                if "LIMIT" in reason or reason == "MISSION_DEADLINE":
                    phase = "LIMIT_REACHED"
                return self._stop(state, phase, reason[:300])
            finally:
                report = self.store.report()
                path = self.store.directory / "report.json"
                temp = path.with_suffix(".json.tmp")
                temp.write_text(canonical(report) + "\n")
                temp.replace(path)
            return self.store.report()

    def accept(self, evidence_hash: str) -> dict:
        """Explicit local acceptance of an already verified, hash-bound delivery only."""
        with self.store.lock():
            state = self.store.load()
            if (state["phase"] != "HUMAN_REQUIRED" or state["reason"] != "FINAL_ACCEPTANCE_REQUIRED"
                    or state["in_flight"] is not None or (state["evidence"] or {}).get("hash") != evidence_hash):
                raise ControlError("ACCEPTANCE_NOT_APPLICABLE")
            self._identity(state)
            state["human_acceptance"] = {"evidence_hash": evidence_hash, "at": time.time()}
            self.store.save(state, "HUMAN_ACCEPTANCE_RECORDED")
            self._seal(state, Contract.parse(state["contract"]))
            return self.store.report()
