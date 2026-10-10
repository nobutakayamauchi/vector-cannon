"""Structured handoff between Vector Cannon and an external supervisor such as ChatGPT dot.

No dot API or identity attestation is assumed. A handoff is a controller-produced,
hash-bound artifact. A returned decision is treated as an external supervisor
instruction and can never override controller verification.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .model import Contract, ControlError, canonical, digest, strict_json
from .store import Store

SUPERVISOR_ACTIONS = {"ACCEPT", "REPAIR", "HUMAN_REQUIRED"}


def _remaining(state: dict, contract: Contract) -> dict:
    limits = contract.limits
    return {
        "worker_shots": max(0, limits["shots"] - state["calls"]["worker"]),
        "jev_calls": max(0, limits["judge_calls"]["jev"] - state["calls"]["jev"]),
        "sol_calls": max(0, limits["judge_calls"]["sol"] - state["calls"]["sol"]),
        "legacy_astra_calls": max(0, limits["judge_calls"]["astra"] - state["calls"]["astra"]),
        "deadline": state["deadline"],
    }


def build_dot_handoff(store: Store, state: dict, contract: Contract, *, reason: str) -> dict:
    evidence = state.get("evidence") or {}
    if not evidence.get("hash") or evidence.get("contract_hash") != contract.hash:
        raise ControlError("DOT_HANDOFF_REQUIRES_CURRENT_EVIDENCE")
    checks = [
        {
            "id": item.get("id"),
            "kind": item.get("kind"),
            "status": item.get("status"),
            "detail": item.get("detail"),
            "artifact_sha256": item.get("artifact_sha256"),
        }
        for item in evidence.get("checks", [])
        if isinstance(item, dict)
    ]
    handoff = {
        "schema": 1,
        "intended_supervisor": "chatgpt-dot",
        "identity_assurance": "NO_DOT_API_OR_CRYPTOGRAPHIC_IDENTITY_ATTESTATION",
        "mission_id": contract.payload["id"],
        "goal": contract.payload["goal"],
        "contract_hash": contract.hash,
        "evidence_hash": evidence["hash"],
        "candidate_revision": state["candidate_revision"],
        "snapshot_hash": state["snapshot_hash"],
        "reason": reason,
        "checks": checks,
        "prior_decisions": list(state.get("decisions") or []),
        "calls": dict(state["calls"]),
        "remaining": _remaining(state, contract),
        "allowed_actions": sorted(SUPERVISOR_ACTIONS),
        "rules": [
            "Do not claim work is complete from an agent summary.",
            "ACCEPT is meaningful only when every controller check is PASS; the controller enforces this.",
            "REPAIR may refine the next attempt but cannot expand scope, permissions, budget, or acceptance criteria.",
            "Use HUMAN_REQUIRED when business intent, authorization, or risk cannot be resolved safely.",
            "This handoff does not authorize deployment, messaging, payment, publishing, or account changes.",
        ],
    }
    handoff["handoff_hash"] = digest(handoff)

    directory = store.directory / "handoff"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    stem = f"dot-r{state['candidate_revision']:04d}-{evidence['hash'][:12]}"
    json_path = directory / f"{stem}.json"
    md_path = directory / f"{stem}.md"
    if json_path.exists() or md_path.exists():
        raise ControlError("DOT_HANDOFF_ALREADY_EXISTS")
    json_path.write_text(canonical(handoff) + "\n", encoding="utf-8")
    json_path.chmod(0o600)

    rows = "\n".join(
        f"- {item['id']}: {item['status']} — {item.get('detail') or 'no detail'}"
        for item in checks
    )
    md_path.write_text(
        "# Vector Cannon → dot handoff\n\n"
        f"Mission: **{contract.payload['id']}**\n\n"
        f"Reason: `{reason}`\n\n"
        f"Evidence hash: `{evidence['hash']}`\n\n"
        "## Controller checks\n"
        f"{rows or '- No checks present'}\n\n"
        "## Return contract\n"
        "Return a JSON decision with exactly: `schema`, `handoff_hash`, `evidence_hash`, "
        "`action`, `summary`. `action` must be `ACCEPT`, `REPAIR`, or `HUMAN_REQUIRED`. "
        "The controller will reject stale or scope-expanding decisions.\n",
        encoding="utf-8",
    )
    md_path.chmod(0o600)
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
    ref = state.get("handoff")
    if not isinstance(ref, dict) or ref.get("kind") != "DOT_HANDOFF":
        raise ControlError("DOT_HANDOFF_NOT_AVAILABLE")
    path = Path(ref["json"])
    if path.is_symlink() or path.resolve().parent != (store.directory / "handoff").resolve():
        raise ControlError("DOT_HANDOFF_PATH_INVALID")
    payload = strict_json(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("handoff_hash") != ref.get("handoff_hash"):
        raise ControlError("DOT_HANDOFF_IDENTITY_MISMATCH")
    copy = dict(payload)
    supplied_hash = copy.pop("handoff_hash", None)
    if digest(copy) != supplied_hash:
        raise ControlError("DOT_HANDOFF_CHANGED")
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
