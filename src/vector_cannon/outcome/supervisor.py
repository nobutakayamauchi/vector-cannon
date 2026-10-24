"""Structured handoff between Vector Cannon and an external supervisor such as ChatGPT dot.

No dot API or identity attestation is assumed. A handoff is a controller-produced,
hash-bound artifact. A returned decision is treated as an external supervisor
instruction and can never override controller verification.
"""
from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from .model import Contract, ControlError, canonical, digest, strict_json
from .store import Store
from .verify import decision_input, redact_value

SUPERVISOR_ACTIONS = {"ACCEPT", "REPAIR", "HUMAN_REQUIRED"}
MAX_HANDOFF_BYTES = 64000


def _remaining(state: dict, contract: Contract) -> dict:
    limits = contract.limits
    return {
        "worker_shots": max(0, limits["shots"] - state["calls"]["worker"]),
        "jev_calls": max(0, limits["judge_calls"]["jev"] - state["calls"]["jev"]),
        "sol_calls": max(0, limits["judge_calls"]["sol"] - state["calls"]["sol"]),
        "legacy_astra_calls": max(0, limits["judge_calls"]["astra"] - state["calls"]["astra"]),
        "deadline": state["deadline"],
    }


def _handoff_directory(store: Store, *, create: bool = False) -> Path:
    directory = store.directory / "handoff"
    if directory.is_symlink():
        raise ControlError("DOT_HANDOFF_PATH_INVALID")
    if create:
        directory.mkdir(exist_ok=True, mode=0o700)
    if not directory.is_dir() or directory.resolve() != directory:
        raise ControlError("DOT_HANDOFF_PATH_INVALID")
    return directory


def _read_artifact(path: Path) -> bytes | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ControlError("DOT_HANDOFF_PATH_INVALID") from exc
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_mode & 0o077 or info.st_size > MAX_HANDOFF_BYTES):
            raise ControlError("DOT_HANDOFF_ARTIFACT_INVALID")
        content = stream.read(MAX_HANDOFF_BYTES + 1)
        if len(content) > MAX_HANDOFF_BYTES:
            raise ControlError("DOT_HANDOFF_TOO_LARGE")
        return content


def _publish_matching(path: Path, content: bytes):
    """Publish complete 0600 files without overwriting conflicts; exact retries are safe."""
    existing = _read_artifact(path)
    if existing is not None:
        if existing != content:
            raise ControlError("DOT_HANDOFF_CONTENT_MISMATCH")
        return
    fd, name = tempfile.mkstemp(prefix=".dot-handoff-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            pass  # Another publisher must still have produced exactly the same artifact.
    finally:
        temporary.unlink(missing_ok=True)
    if _read_artifact(path) != content:
        raise ControlError("DOT_HANDOFF_CONTENT_MISMATCH")


def build_dot_handoff(store: Store, state: dict, contract: Contract, *, reason: str) -> dict:
    if contract.payload["data_policy"] != "redacted_evidence":
        raise ControlError("EXTERNAL_EVIDENCE_NOT_AUTHORIZED")
    evidence = state.get("evidence") or {}
    evidence_body = {k: v for k, v in evidence.items() if k != "hash"}
    if (not evidence.get("hash") or evidence.get("contract_hash") != contract.hash
            or evidence.get("snapshot_hash") != state["snapshot_hash"]
            or evidence.get("revision") != state["candidate_revision"]
            or digest(evidence_body) != evidence["hash"]):
        raise ControlError("DOT_HANDOFF_REQUIRES_CURRENT_EVIDENCE")
    review = decision_input(contract, evidence)
    handoff = redact_value({
        "schema": 1,
        "intended_supervisor": "chatgpt-dot",
        "identity_assurance": "NO_DOT_API_OR_CRYPTOGRAPHIC_IDENTITY_ATTESTATION",
        "mission_id": contract.payload["id"],
        "goal": review["goal"],
        "criteria": review["criteria"],
        "contract_hash": contract.hash,
        "evidence_hash": evidence["hash"],
        "candidate_revision": state["candidate_revision"],
        "snapshot_hash": state["snapshot_hash"],
        "reason": reason,
        "checks": review["facts"],
        "prior_decisions": list(state.get("decisions") or []),
        "calls": dict(state["calls"]),
        "remaining": _remaining(state, contract),
        "allowed_actions": sorted(SUPERVISOR_ACTIONS),
        "rules": [
            "Do not claim work is complete from an agent summary.",
            "Inspect criteria and artifact excerpts, not merely PASS metadata.",
            "Excerpts are bounded and may be redacted or absent; insufficient evidence requires HUMAN_REQUIRED.",
            "ACCEPT is meaningful only when every controller check is PASS; the controller enforces this.",
            "REPAIR may refine the next attempt but cannot expand scope, permissions, budget, or acceptance criteria.",
            "Use HUMAN_REQUIRED when business intent, authorization, or risk cannot be resolved safely.",
            "This handoff does not authorize deployment, messaging, payment, publishing, or account changes.",
        ],
    })
    handoff["handoff_hash"] = digest(handoff)
    json_bytes = (canonical(handoff) + "\n").encode("utf-8")
    rows = "\n".join(
        f"- {item['id']}: {item['status']} — {item.get('detail') or 'no detail'}"
        for item in handoff["checks"]
    )
    markdown = (
        "# Vector Cannon → dot handoff\n\n"
        f"Mission: **{handoff['mission_id']}**\n\n"
        f"Goal: {handoff['goal']}\n\n"
        f"Reason: `{handoff['reason']}`\n\n"
        f"Evidence hash: `{handoff['evidence_hash']}`\n\n"
        "## Controller checks\n"
        f"{rows or '- No checks present'}\n\n"
        "Read the paired JSON criteria and bounded artifact excerpts before deciding. "
        "PASS alone is not semantic proof. Missing, truncated, or redacted evidence may require HUMAN_REQUIRED.\n\n"
        "## Return contract\n"
        "Return a JSON decision with exactly: `schema`, `handoff_hash`, `evidence_hash`, "
        "`action`, `summary`. `action` must be `ACCEPT`, `REPAIR`, or `HUMAN_REQUIRED`. "
        "The controller will reject stale or scope-expanding decisions.\n"
    ).encode("utf-8")
    if max(len(json_bytes), len(markdown)) > MAX_HANDOFF_BYTES:
        raise ControlError("DOT_HANDOFF_TOO_LARGE")

    directory = _handoff_directory(store, create=True)
    stem = f"dot-r{state['candidate_revision']:04d}-{evidence['hash'][:12]}"
    json_path, md_path = directory / f"{stem}.json", directory / f"{stem}.md"
    _publish_matching(json_path, json_bytes)
    _publish_matching(md_path, markdown)
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return {
        "kind": "DOT_HANDOFF",
        "json": str(json_path),
        "markdown": str(md_path),
        "handoff_hash": handoff["handoff_hash"],
        "evidence_hash": evidence["hash"],
        "candidate_revision": state["candidate_revision"],
        "identity_assurance": handoff["identity_assurance"],
    }


def load_handoff(store: Store, state: dict) -> dict:
    if state.get("phase") != "DOT_HANDOFF":
        raise ControlError("DOT_HANDOFF_NOT_CURRENT")
    contract = Contract.parse(state["contract"])
    if contract.payload["data_policy"] != "redacted_evidence":
        raise ControlError("EXTERNAL_EVIDENCE_NOT_AUTHORIZED")
    ref = state.get("handoff")
    if not isinstance(ref, dict) or ref.get("kind") != "DOT_HANDOFF":
        raise ControlError("DOT_HANDOFF_NOT_AVAILABLE")
    directory = _handoff_directory(store)
    path = Path(ref["json"])
    if path.parent != directory:
        raise ControlError("DOT_HANDOFF_PATH_INVALID")
    content = _read_artifact(path)
    if content is None:
        raise ControlError("DOT_HANDOFF_NOT_AVAILABLE")
    payload = strict_json(content.decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("handoff_hash") != ref.get("handoff_hash"):
        raise ControlError("DOT_HANDOFF_IDENTITY_MISMATCH")
    copy = dict(payload)
    supplied_hash = copy.pop("handoff_hash", None)
    if digest(copy) != supplied_hash:
        raise ControlError("DOT_HANDOFF_CHANGED")
    if not payload.get("criteria") or redact_value(payload) != payload:
        raise ControlError("DOT_HANDOFF_REBUILD_REQUIRED")
    evidence = state.get("evidence") or {}
    if (payload.get("contract_hash") != contract.hash
            or payload.get("evidence_hash") != evidence.get("hash")
            or payload.get("snapshot_hash") != state["snapshot_hash"]
            or payload.get("candidate_revision") != state["candidate_revision"]):
        raise ControlError("DOT_HANDOFF_REQUIRES_CURRENT_EVIDENCE")
    return payload


def parse_supervisor_decision(raw: Any, *, handoff: dict) -> dict:
    if not isinstance(raw, dict) or set(raw) != {
        "schema", "handoff_hash", "evidence_hash", "action", "summary"
    }:
        raise ControlError("INVALID_SUPERVISOR_DECISION_FIELDS")
    data = strict_json(canonical(raw))
    if type(data["schema"]) is not int or data["schema"] != 1:
        raise ControlError("UNSUPPORTED_SUPERVISOR_DECISION_SCHEMA")
    if data["handoff_hash"] != handoff["handoff_hash"]:
        raise ControlError("STALE_SUPERVISOR_HANDOFF")
    if data["evidence_hash"] != handoff["evidence_hash"]:
        raise ControlError("STALE_SUPERVISOR_EVIDENCE")
    if data["action"] not in SUPERVISOR_ACTIONS:
        raise ControlError("INVALID_SUPERVISOR_ACTION")
    if not isinstance(data["summary"], str) or not 1 <= len(data["summary"].strip()) <= 4000:
        raise ControlError("INVALID_SUPERVISOR_SUMMARY")
    return data


def submit_supervisor_decision(store: Store, raw: Any) -> dict:
    with store.lock():
        state = store.load()
        if state["phase"] != "DOT_HANDOFF" or state.get("in_flight") is not None:
            raise ControlError("SUPERVISOR_DECISION_NOT_APPLICABLE")
        contract = Contract.parse(state["contract"])
        handoff = load_handoff(store, state)
        decision = parse_supervisor_decision(raw, handoff=handoff)
        evidence = state.get("evidence") or {}
        if evidence.get("hash") != decision["evidence_hash"]:
            raise ControlError("SUPERVISOR_EVIDENCE_NO_LONGER_CURRENT")
        checks = evidence.get("checks", [])
        state["supervisor_decision"] = {
            "kind": "EXTERNAL_SUPERVISOR_DECISION",
            "identity_assurance": "UNATTESTED_EXTERNAL_SUPERVISOR",
            "handoff_hash": decision["handoff_hash"],
            "evidence_hash": decision["evidence_hash"],
            "action": decision["action"],
            "summary": decision["summary"].strip(),
        }
        if decision["action"] == "ACCEPT":
            if not checks or not all(
                item.get("status") == "PASS" and item.get("producer") == "CONTROLLER"
                for item in checks
            ):
                raise ControlError("SUPERVISOR_CANNOT_OVERRIDE_FAILED_CHECKS")
            state.update(phase="SEAL", reason="EXTERNAL_SUPERVISOR_ACCEPTED")
        elif decision["action"] == "REPAIR":
            if state["calls"]["worker"] >= contract.limits["shots"]:
                state.update(phase="LIMIT_REACHED", reason="WORKER_CALL_LIMIT")
            elif state["no_progress"] >= contract.limits["no_progress"]:
                state.update(phase="HUMAN_REQUIRED", reason="NO_PROGRESS")
            else:
                state["supervisor_note"] = decision["summary"].strip()
                state.update(phase="READY", reason="EXTERNAL_SUPERVISOR_REPAIR_REQUESTED")
        else:
            state.update(phase="HUMAN_REQUIRED", reason="EXTERNAL_SUPERVISOR_REQUESTED_HUMAN")
        store.save(state, "EXTERNAL_SUPERVISOR_DECISION")
        return store.report()
