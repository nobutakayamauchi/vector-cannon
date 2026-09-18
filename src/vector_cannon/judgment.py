from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class Verdict(str, Enum):
    DONE = "DONE"
    CONTINUE = "CONTINUE"
    REPAIR = "REPAIR"
    REVIEW = "REVIEW"
    ESCALATE = "ESCALATE"
    BLOCKED = "BLOCKED"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


CONFIDENCE = {"low", "medium", "high"}
ESCALATION_TARGETS = {"sol", "astra", "human"}


@dataclass(frozen=True)
class AcceptanceAssessment:
    satisfied: tuple[str, ...] = ()
    unsatisfied: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        groups = (self.satisfied, self.unsatisfied, self.unknown)
        if any(not isinstance(item, str) or not item.strip() for group in groups for item in group):
            raise ValueError("acceptance ids must be non-empty strings")
        all_ids = [item for group in groups for item in group]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("an acceptance id may appear in only one completion bucket")


@dataclass(frozen=True)
class NextAction:
    kind: str
    goal: str
    allowed_scope: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("next_action.kind must not be empty")
        if not self.goal.strip():
            raise ValueError("next_action.goal must not be empty")
        if any(not isinstance(item, str) or not item.strip() for item in self.allowed_scope):
            raise ValueError("next_action.allowed_scope entries must be non-empty strings")


@dataclass(frozen=True)
class Judgment:
    judge: str
    verdict: Verdict
    confidence: str
    completion: AcceptanceAssessment
    next_action: NextAction | None
    escalate_to: str | None
    reason_codes: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    summary: str

    def __post_init__(self) -> None:
        if not self.judge.strip():
            raise ValueError("judge must not be empty")
        if self.confidence not in CONFIDENCE:
            raise ValueError("confidence must be low, medium, or high")
        if self.escalate_to is not None and self.escalate_to not in ESCALATION_TARGETS:
            raise ValueError("escalate_to must be sol, astra, human, or null")
        if any(not isinstance(item, str) or not item.strip() for item in self.reason_codes):
            raise ValueError("reason_codes must contain non-empty strings")
        if any(not isinstance(item, str) or not item.strip() for item in self.evidence_refs):
            raise ValueError("evidence_refs must contain non-empty strings")
        if not self.summary.strip():
            raise ValueError("summary must not be empty")

        if self.verdict is Verdict.DONE:
            if self.completion.unsatisfied or self.completion.unknown:
                raise ValueError("DONE requires no unsatisfied or unknown acceptance criteria")
            if self.next_action is not None or self.escalate_to is not None:
                raise ValueError("DONE cannot include a next action or escalation")
        elif self.verdict in {Verdict.CONTINUE, Verdict.REPAIR, Verdict.REVIEW}:
            if self.next_action is None:
                raise ValueError(f"{self.verdict.value} requires next_action")
            if self.escalate_to is not None:
                raise ValueError(f"{self.verdict.value} cannot include escalate_to")
        elif self.verdict is Verdict.ESCALATE:
            if self.escalate_to is None:
                raise ValueError("ESCALATE requires escalate_to")
        elif self.verdict is Verdict.HUMAN_REQUIRED:
            if self.escalate_to not in {None, "human"}:
                raise ValueError("HUMAN_REQUIRED may only escalate to human")

    @classmethod
    def from_dict(cls, payload: dict[str, Any], *, judge: str) -> "Judgment":
        expected = {
            "verdict", "confidence", "completion", "next_action", "escalate_to",
            "reason_codes", "evidence_refs", "summary",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("judgment payload does not match the required fields")

        completion_raw = payload["completion"]
        if not isinstance(completion_raw, dict) or set(completion_raw) != {"satisfied", "unsatisfied", "unknown"}:
            raise ValueError("completion must contain satisfied, unsatisfied, and unknown")
        completion = AcceptanceAssessment(
            satisfied=_string_tuple(completion_raw["satisfied"], "completion.satisfied"),
            unsatisfied=_string_tuple(completion_raw["unsatisfied"], "completion.unsatisfied"),
            unknown=_string_tuple(completion_raw["unknown"], "completion.unknown"),
        )

        action_raw = payload["next_action"]
        action = None
        if action_raw is not None:
            if not isinstance(action_raw, dict) or set(action_raw) != {"kind", "goal", "allowed_scope"}:
                raise ValueError("next_action must contain kind, goal, and allowed_scope")
            if not isinstance(action_raw["kind"], str) or not isinstance(action_raw["goal"], str):
                raise ValueError("next_action kind and goal must be strings")
            action = NextAction(
                kind=action_raw["kind"],
                goal=action_raw["goal"],
                allowed_scope=_string_tuple(action_raw["allowed_scope"], "next_action.allowed_scope"),
            )

        try:
            verdict = Verdict(payload["verdict"])
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid verdict") from exc
        if not isinstance(payload["confidence"], str):
            raise ValueError("confidence must be a string")
        escalate_to = payload["escalate_to"]
        if escalate_to is not None and not isinstance(escalate_to, str):
            raise ValueError("escalate_to must be a string or null")
        if not isinstance(payload["summary"], str):
            raise ValueError("summary must be a string")

        return cls(
            judge=judge,
            verdict=verdict,
            confidence=payload["confidence"],
            completion=completion,
            next_action=action,
            escalate_to=escalate_to,
            reason_codes=_string_tuple(payload["reason_codes"], "reason_codes"),
            evidence_refs=_string_tuple(payload["evidence_refs"], "evidence_refs"),
            summary=payload["summary"],
        )


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be an array of strings")
    return tuple(value)


JUDGMENT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "verdict", "confidence", "completion", "next_action", "escalate_to",
        "reason_codes", "evidence_refs", "summary",
    ],
    "properties": {
        "verdict": {"enum": [item.value for item in Verdict]},
        "confidence": {"enum": sorted(CONFIDENCE)},
        "completion": {
            "type": "object",
            "additionalProperties": False,
            "required": ["satisfied", "unsatisfied", "unknown"],
            "properties": {
                "satisfied": {"type": "array", "items": {"type": "string"}},
                "unsatisfied": {"type": "array", "items": {"type": "string"}},
                "unknown": {"type": "array", "items": {"type": "string"}},
            },
        },
        "next_action": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "goal", "allowed_scope"],
                    "properties": {
                        "kind": {"type": "string"},
                        "goal": {"type": "string"},
                        "allowed_scope": {"type": "array", "items": {"type": "string"}},
                    },
                },
            ]
        },
        "escalate_to": {"anyOf": [{"type": "null"}, {"enum": sorted(ESCALATION_TARGETS)}]},
        "reason_codes": {"type": "array", "items": {"type": "string"}},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
}
