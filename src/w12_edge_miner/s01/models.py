from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Iterable


class State(StrEnum):
    VERIFIED = "VERIFIED"
    UNKNOWN = "UNKNOWN"
    DOCUMENTATION_ONLY = "DOCUMENTATION_ONLY"
    SOURCE_UNSUPPORTED = "SOURCE_UNSUPPORTED"
    NETWORK_UNAVAILABLE = "NETWORK_UNAVAILABLE"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    ACCESS_DENIED = "ACCESS_DENIED"
    FIELD_MISSING = "FIELD_MISSING"
    CAUSALITY_UNPROVEN = "CAUSALITY_UNPROVEN"


# Input cannot relax this list, supply a final status, or mark fields optional.
MANDATORY = (
    "source_identity", "wallet_identity", "wallet_discovery", "exact_product",
    "side", "filled_amount", "stable_event_id", "event_time", "observed_at",
    "available_at", "open", "increase", "reduce", "close", "fees",
    "realized_pnl", "historical_mtm", "historical_coverage", "pagination",
    "rate_limits", "finality_revisions", "forward_timeliness", "read_auth",
    "read_only_boundary", "causal_history",
)


def utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    kind: str
    title: str
    uri: str
    retrieved_at: str
    claim: str
    sha256: str | None = None
    fixture: bool = False

    def __post_init__(self) -> None:
        if not all((self.evidence_id, self.title, self.uri, self.claim)):
            raise ValueError("evidence identity, source and exact claim are required")
        if self.kind not in {"REPOSITORY", "DOCUMENTATION", "READ_ONLY_PROBE", "CAUSAL_CAPTURE"}:
            raise ValueError("unknown evidence kind")
        utc(self.retrieved_at)
        if self.sha256 is not None and (
            len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256)
        ):
            raise ValueError("invalid evidence SHA-256")


@dataclass(frozen=True)
class Capability:
    name: str
    state: State
    detail: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class TimingObservation:
    """Timing fixture interface, not a canonical event/position schema.

    received_at is a local receipt, never the source payload's observed_at.
    finality_observed_at is the local observation of a usable finality state,
    not an old block timestamp fetched today. None is not replaced by zero.
    """

    event_id: str | None
    event_time: str | None
    observed_at: str | None
    finality_observed_at: str | None
    prerequisites: tuple[str | None, ...] = ()

    def __post_init__(self) -> None:
        for value in (self.event_time, self.observed_at, self.finality_observed_at, *self.prerequisites):
            if value is not None:
                utc(value)

    @classmethod
    def received(
        cls, payload: dict[str, Any], *, received_at: str | None,
        finality_observed_at: str | None = None,
        prerequisites: tuple[str | None, ...] = (),
    ) -> TimingObservation:
        # Payload-supplied observed_at/available_at/finality are untrusted.
        return cls(payload.get("event_id"), payload.get("event_time"), received_at,
                   finality_observed_at, prerequisites)

    @property
    def available_at(self) -> str | None:
        if not self.event_id or self.event_time is None or self.observed_at is None or self.finality_observed_at is None:
            return None
        if any(t is None for t in self.prerequisites):
            return None
        event, received, finality = map(utc, (self.event_time, self.observed_at, self.finality_observed_at))
        if event > received or event > finality:
            return None  # inconsistent clock/source evidence; fail closed
        return max(received, finality, *(utc(t) for t in self.prerequisites)).isoformat()


def available_as_of(rows: Iterable[TimingObservation], boundary: str) -> tuple[str, ...]:
    cutoff = utc(boundary)
    # Filter each immutable receipt before deduplication. A later revision or
    # a newly fetched old row cannot repair/erase a past observation.
    return tuple(sorted({row.event_id for row in rows
                         if row.available_at is not None and utc(row.available_at) <= cutoff}))


@dataclass(frozen=True)
class CausalWitness:
    observation: TimingObservation
    evidence_id: str
    decision_boundary: str

    def __post_init__(self) -> None:
        utc(self.decision_boundary)


@dataclass(frozen=True)
class CapabilityAudit:
    source_id: str | None
    venue_product: str | None
    scope: dict[str, Any]
    capabilities: tuple[Capability, ...]
    provenance: tuple[Evidence, ...]
    causal_witnesses: tuple[CausalWitness, ...] = ()

    def __post_init__(self) -> None:
        for items in ([c.name for c in self.capabilities], [p.evidence_id for p in self.provenance]):
            if len(items) != len(set(items)):
                raise ValueError("duplicate capability/evidence identifier")
        known = {p.evidence_id for p in self.provenance}
        if any(set(c.evidence_ids) - known for c in self.capabilities):
            raise ValueError("unresolved evidence reference")
        if any(w.evidence_id not in known for w in self.causal_witnesses):
            raise ValueError("unresolved witness evidence")

    @property
    def blockers(self) -> list[dict[str, str]]:
        caps = {c.name: c for c in self.capabilities}
        evidence = {p.evidence_id: p for p in self.provenance}
        blockers = []
        if not self.source_id or not self.venue_product:
            blockers.append({"field": "source_identity", "code": "FIELD_MISSING", "detail": "Source/product unresolved"})
        for name in MANDATORY:
            c = caps.get(name)
            if c is None:
                blockers.append({"field": name, "code": "FIELD_MISSING", "detail": "No capability evidence supplied"})
            elif c.state != State.VERIFIED:
                blockers.append({"field": name, "code": c.state.value, "detail": c.detail})
            elif not c.evidence_ids or any(evidence[k].fixture for k in c.evidence_ids):
                blockers.append({"field": name, "code": "CAUSALITY_UNPROVEN", "detail": "Real provenance required; fixtures do not establish source capability"})
        # Documentation or a GET returning old records cannot establish when a
        # forward observer could first have used those records.
        causal = caps.get("causal_history")
        if causal is not None and causal.state == State.VERIFIED and not any(
            evidence[k].kind == "CAUSAL_CAPTURE" and evidence[k].sha256 and not evidence[k].fixture
            for k in causal.evidence_ids
        ):
            blockers.append({"field": "causal_history", "code": "CAUSALITY_UNPROVEN", "detail": "Requires an independently audited causal capture, not current historical GETs"})
        valid_witness = any(
            evidence[w.evidence_id].kind == "CAUSAL_CAPTURE"
            and evidence[w.evidence_id].sha256 and not evidence[w.evidence_id].fixture
            and available_as_of((w.observation,), w.decision_boundary)
            for w in self.causal_witnesses
        )
        if not valid_witness:
            blockers.append({"field": "causal_witness", "code": "CAUSALITY_UNPROVEN", "detail": "No provenance-backed receipt/finality witness usable at the decision boundary"})
        return blockers

    def to_payload(self) -> dict[str, Any]:
        blockers = self.blockers
        return {
            "schema": "W12_EDGE_MINER_S01_CAPABILITY_V1",
            "source_id": self.source_id, "venue_product": self.venue_product,
            "scope": self.scope, "mandatory_capabilities": list(MANDATORY),
            "capabilities": [asdict(c) for c in sorted(self.capabilities, key=lambda c: c.name)],
            "provenance": [asdict(p) for p in sorted(self.provenance, key=lambda p: p.evidence_id)],
            "causal_witnesses": [asdict(w) for w in self.causal_witnesses],
            "blockers": blockers,
            "final_status": "BLOCKED_WALLET_DATA_SOURCE" if blockers else "PASS_READ_ONLY_SOURCE",
        }


def load_audit(data: dict[str, Any]) -> CapabilityAudit:
    if any(k in data for k in ("final_status", "mandatory_capabilities", "live_enabled")):
        raise ValueError("input cannot override audit policy")
    return CapabilityAudit(
        source_id=data.get("source_id"), venue_product=data.get("venue_product"),
        scope=data.get("scope", {}),
        capabilities=tuple(Capability(c["name"], State(c["state"]), c["detail"], tuple(c.get("evidence_ids", ())))
                           for c in data.get("capabilities", ())),
        provenance=tuple(Evidence(**p) for p in data.get("provenance", ())),
        causal_witnesses=tuple(CausalWitness(
            TimingObservation(**{**w["observation"], "prerequisites": tuple(w["observation"].get("prerequisites", ())) }),
            w["evidence_id"], w["decision_boundary"])
            for w in data.get("causal_witnesses", ())),
    )
