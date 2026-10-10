"""Local transactional state. Reserve external operations before invoking them."""
from __future__ import annotations

import contextlib
import fcntl
import os
import sqlite3
import time
from decimal import Decimal
from pathlib import Path

from .model import ControlError, Contract, ROLES, canonical, digest, manifest, money, strict_json

TERMINAL = {"VERIFIED_DONE", "HUMAN_REQUIRED", "BLOCKED", "LIMIT_REACHED", "DOT_HANDOFF"}


class Store:
    def __init__(self, directory: Path):
        self.directory = directory.expanduser().resolve()
        self.path = self.directory / "control.sqlite3"

    @classmethod
    def create(cls, directory: Path, contract: Contract, identity: dict, *, now: float | None = None):
        if directory.exists() or directory.is_symlink():
            raise ControlError("SESSION_ALREADY_EXISTS")
        directory.mkdir(parents=True, mode=0o700)
        store = cls(directory)
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
                CREATE TABLE events (seq INTEGER PRIMARY KEY, at REAL NOT NULL,
                                     kind TEXT NOT NULL, state_hash TEXT NOT NULL);
            """)
        timestamp = time.time() if now is None else now
        store.save({
            "schema": 1, "contract": contract.payload, "contract_hash": contract.hash,
            "identity": identity, "phase": "READY", "reason": "CREATED", "generation": 0,
            "started_at": timestamp, "deadline": timestamp + contract.limits["seconds"],
            "snapshot": "baseline", "snapshot_hash": identity["source_hash"],
            "calls": {role: 0 for role in ROLES}, "operations": [], "evidence": None,
            "decisions": [], "no_progress": 0, "in_flight": None, "last_receipt": None,
            "known_cost_usd": "0", "committed_usd": "0", "unknown_cost_calls": 0,
            "cost_status": "NO_CALLS", "candidate_revision": 0, "output": None,
            "handoff": None, "supervisor_decision": None, "supervisor_note": None,
        }, "CREATED")
        store.path.chmod(0o600)
        return store

    @contextlib.contextmanager
    def connect(self, *, readonly: bool = False):
        if self.path.is_symlink():
            raise ControlError("STATE_SYMLINK")
        if readonly:
            db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=5)
        else:
            db = sqlite3.connect(self.path, timeout=5)
            db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextlib.contextmanager
    def lock(self):
        if not self.path.is_file():
            raise ControlError("SESSION_NOT_FOUND")
        fd = os.open(self.directory / "controller.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ControlError("SESSION_BUSY") from exc
            yield fd
        finally:
            os.close(fd)

    def load(self) -> dict:
        with self.connect(readonly=True) as db:
            row = db.execute("SELECT body FROM state WHERE id=1").fetchone()
            last = db.execute("SELECT state_hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        if row is None or last is None:
            raise ControlError("STATE_MISSING")
        state = strict_json(row[0])
        if digest(state) != last[0] or Contract.parse(state["contract"]).hash != state["contract_hash"]:
            raise ControlError("STATE_IDENTITY_MISMATCH")
        return state

    def save(self, state: dict, event: str):
        """Called under controller.lock after creation. SQLite commits state + event together."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT body FROM state WHERE id=1").fetchone()
            if previous is not None:
                observed = strict_json(previous[0])["generation"]
                if observed != state["generation"]:
                    raise ControlError("STALE_STATE_WRITE")
            state["generation"] += 1
            db.execute("INSERT OR REPLACE INTO state VALUES(1,?)", (canonical(state),))
            db.execute("INSERT INTO events VALUES(?,?,?,?)",
                       (state["generation"], time.time(), event, digest(state)))

    def reserve(self, state: dict, role: str, *, now: float | None = None) -> str:
        if role not in ROLES or state["in_flight"] is not None:
            raise ControlError("OPERATION_ALREADY_IN_FLIGHT")
        limits = state["contract"]["limits"]
        now = time.time() if now is None else now
        if now >= state["deadline"]:
            raise ControlError("MISSION_DEADLINE")
        cap = limits["shots"] if role == "worker" else limits["judge_calls"][role]
        if state["calls"][role] >= cap:
            raise ControlError(f"{role.upper()}_CALL_LIMIT")
        if state["unknown_cost_calls"] and limits["unknown_cost"] == "stop":
            raise ControlError("UNRECONCILED_COST")
        reserved, maximum = money(limits["reserve_usd"][role]), money(limits["max_usd"])
        if maximum is not None and reserved is None:
            raise ControlError("COST_RESERVATION_REQUIRED")
        if reserved is None and limits["unknown_cost"] == "stop":
            raise ControlError("COST_BOUND_UNKNOWN")
        committed = money(state["committed_usd"]) + (reserved or Decimal(0))
        if maximum is not None and committed > maximum:
            raise ControlError("MISSION_COST_LIMIT")
        state["calls"][role] += 1
        operation_id = f"{role}-{state['calls'][role]:03d}"
        state["operations"].append({"id": operation_id, "role": role, "state": "RESERVED",
                                    "reserved_usd": str(reserved) if reserved is not None else None,
                                    "observed_usd": None, "cost_status": "UNKNOWN", "at": now})
        state["committed_usd"] = str(committed)
        state["in_flight"] = operation_id
        self.save(state, "OPERATION_RESERVED")
        return operation_id

    def finish(self, state: dict, receipt: dict, next_phase: str):
        operation = state["operations"][-1]
        if operation["id"] != state["in_flight"]:
            raise ControlError("OPERATION_ID_MISMATCH")
        observed = money(receipt.get("cost_usd"))
        cost_status = receipt.get("cost_status", "UNKNOWN")
        if cost_status not in {"MEASURED", "DERIVED", "ESTIMATED", "UNKNOWN"}:
            raise ControlError("INVALID_COST_STATUS")
        # Estimated prices are never relabelled as observed bills.
        if observed is None or cost_status not in {"MEASURED", "DERIVED"} or receipt.get("external_cost_unknown"):
            state["unknown_cost_calls"] += 1
        else:
            state["known_cost_usd"] = str(money(state["known_cost_usd"]) + observed)
        reserved = money(operation["reserved_usd"]) or Decimal(0)
        if observed is not None and observed > reserved:
            state["committed_usd"] = str(money(state["committed_usd"]) + observed - reserved)
        operation.update(state="RETURNED", observed_usd=str(observed) if observed is not None else None,
                         cost_status=cost_status)
        state["cost_status"] = "PARTIAL_OR_UNKNOWN" if state["unknown_cost_calls"] else "OBSERVED"
        state["last_receipt"] = receipt
        state["in_flight"] = None
        state["phase"] = next_phase
        maximum = money(state["contract"]["limits"]["max_usd"])
        if maximum is not None and money(state["committed_usd"]) > maximum:
            state.update(phase="LIMIT_REACHED", reason="OBSERVED_COST_EXCEEDS_LIMIT")
        self.save(state, "OPERATION_RETURNED")

    def report(self) -> dict:
        state = self.load()
        evidence = state["evidence"] or {}
        checks = evidence.get("checks", [])
        integrity = "NOT_SEALED"
        phase, reason = state["phase"], state["reason"]
        if state["phase"] == "VERIFIED_DONE":
            import hashlib
            try:
                output = state["output"]
                intact = digest(manifest(Path(output["directory"]))) == output["snapshot_hash"]
                intact = intact and hashlib.sha256(Path(output["patch"]).read_bytes()).hexdigest() == output["patch_sha256"]
                integrity = "VERIFIED" if intact else "FAILED"
            except (OSError, ValueError, TypeError):
                integrity = "FAILED"
            if integrity == "FAILED":
                phase, reason = "BLOCKED", "DELIVERY_CHANGED_SINCE_SEAL"
        return {
            "delivery_integrity": integrity,
            "cost_scope": "Controller operations only; external generation, human labor and subscription fees excluded.",
            "mission_id": state["contract"]["id"], "state": phase, "reason": reason,
            "contract_hash": state["contract_hash"], "candidate_revision": state["candidate_revision"],
            "snapshot_hash": state["snapshot_hash"], "evidence_hash": evidence.get("hash"),
            "criteria_passed": sum(c["status"] == "PASS" for c in checks),
            "criteria_total": len(state["contract"]["criteria"]), "calls": state["calls"],
            "known_cost_usd": state["known_cost_usd"], "reserved_or_observed_usd": state["committed_usd"],
            "cost_status": state["cost_status"], "unknown_cost_calls": state["unknown_cost_calls"],
            "quota": {"value": None, "status": "UNKNOWN"},
            "cost_per_verified_outcome_usd": (
                state["known_cost_usd"] if phase == "VERIFIED_DONE" and not state["unknown_cost_calls"] else None
            ),
            "decisions": state["decisions"], "handoff": state.get("handoff"),
            "supervisor_decision": state.get("supervisor_decision"),
            "output": state["output"], "in_flight": state["in_flight"],
            "deployment": "NOT_PERFORMED", "source_modified_by_controller": False,
            "execution_assurance": state["identity"].get("agent"),
        }
