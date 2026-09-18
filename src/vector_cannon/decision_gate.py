from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from .direct import DirectShot
from .evidence import EvidencePack
from .judgment import JUDGMENT_JSON_SCHEMA, Judgment, Verdict


class JudgeError(RuntimeError):
    pass


class Judge(Protocol):
    name: str

    def judge(self, evidence: EvidencePack) -> Judgment:
        ...


@dataclass(frozen=True)
class DecisionTrace:
    judgments: tuple[Judgment, ...]
    final: Judgment

    @property
    def judges_used(self) -> tuple[str, ...]:
        return tuple(item.judge for item in self.judgments)

    @property
    def escalated(self) -> bool:
        return len(self.judgments) > 1

    def count(self, judge: str) -> int:
        return sum(1 for item in self.judgments if item.judge == judge)

    def to_payload(self) -> dict:
        return {
            "judges_used": list(self.judges_used),
            "judge_counts": {
                "jev": self.count("jev"),
                "sol": self.count("sol"),
                "astra": self.count("astra"),
            },
            "escalated": self.escalated,
            "final": _judgment_payload(self.final),
            "judgments": [_judgment_payload(item) for item in self.judgments],
        }


class DecisionGate:
    """Jev-first completion gate with bounded escalation to Sol and Astra.

    The gate deliberately does not let Jev skip directly to Astra when Sol is
    available. Astra is the last automated court, not the default reviewer.
    """

    def __init__(self, jev: Judge, *, sol: Judge | None = None, astra: Judge | None = None) -> None:
        self.jev = jev
        self.sol = sol
        self.astra = astra

    def decide(
        self,
        evidence: EvidencePack,
        *,
        allow_sol: bool = True,
        allow_astra: bool = True,
    ) -> DecisionTrace:
        judgments: list[Judgment] = []

        jev_fallback = self.sol if (allow_sol and self.sol is not None) else (
            self.astra if (allow_astra and self.astra is not None) else None
        )
        primary = self._call_or_synthetic_escalation(
            self.jev,
            evidence,
            fallback=jev_fallback,
        )
        judgments.append(primary)

        if primary.verdict is Verdict.ESCALATE and primary.escalate_to == "human":
            return DecisionTrace(tuple(judgments), primary)
        if not self._needs_escalation(primary):
            return DecisionTrace(tuple(judgments), primary)

        if allow_sol and self.sol is not None:
            sol_fallback = self.astra if (allow_astra and self.astra is not None) else None
            sol = self._call_or_synthetic_escalation(
                self.sol,
                evidence,
                fallback=sol_fallback,
            )
            judgments.append(sol)
            if sol.verdict is Verdict.ESCALATE and sol.escalate_to == "human":
                return DecisionTrace(tuple(judgments), sol)
            if not self._needs_escalation(sol):
                return DecisionTrace(tuple(judgments), sol)

        if allow_astra and self.astra is not None:
            try:
                astra = self._call(self.astra, evidence)
            except JudgeError:
                terminal = self._human_required(
                    evidence,
                    judge="controller",
                    reason_code="ASTRA_OUTPUT_INVALID",
                    summary="Astra did not return a valid typed judgment.",
                )
                judgments.append(terminal)
                return DecisionTrace(tuple(judgments), terminal)
            judgments.append(astra)
            if astra.verdict is Verdict.ESCALATE and astra.escalate_to != "human":
                terminal = self._human_required(
                    evidence,
                    judge="controller",
                    reason_code="AI_ESCALATION_EXHAUSTED",
                    summary="Automated judgment tiers are exhausted.",
                )
                judgments.append(terminal)
                return DecisionTrace(tuple(judgments), terminal)
            return DecisionTrace(tuple(judgments), astra)

        terminal = self._human_required(
            evidence,
            judge="controller",
            reason_code="DECISION_BUDGET_EXHAUSTED",
            summary="The next automated judgment tier is not available within this mission budget.",
        )
        judgments.append(terminal)
        return DecisionTrace(tuple(judgments), terminal)

    @staticmethod
    def _needs_escalation(judgment: Judgment) -> bool:
        if judgment.verdict is Verdict.ESCALATE:
            return judgment.escalate_to != "human"
        # Completion is the most consequential decision. Low-confidence DONE
        # must climb at least one tier before the controller accepts it.
        if judgment.verdict is Verdict.DONE:
            return judgment.confidence == "low"
        return False

    def _call_or_synthetic_escalation(
        self,
        judge: Judge,
        evidence: EvidencePack,
        *,
        fallback: Judge | None,
    ) -> Judgment:
        try:
            return self._call(judge, evidence)
        except JudgeError:
            if fallback is None:
                return self._human_required(
                    evidence,
                    judge="controller",
                    reason_code="JUDGE_OUTPUT_INVALID",
                    summary=f"{judge.name} did not return a valid typed judgment and no automated fallback is available.",
                )
            return Judgment.from_dict(
                {
                    "verdict": "ESCALATE",
                    "confidence": "low",
                    "completion": {
                        "satisfied": [],
                        "unsatisfied": [],
                        "unknown": [item.criterion_id for item in evidence.acceptance_criteria],
                    },
                    "next_action": None,
                    "escalate_to": "astra" if fallback.name == "astra" else "sol",
                    "reason_codes": ["JUDGE_OUTPUT_INVALID"],
                    "evidence_refs": [],
                    "summary": f"{judge.name} did not return a valid typed judgment.",
                },
                judge=judge.name,
            )

    @staticmethod
    def _call(judge: Judge, evidence: EvidencePack) -> Judgment:
        try:
            result = judge.judge(evidence)
        except JudgeError:
            raise
        except Exception as exc:
            raise JudgeError(f"{judge.name} judge failed") from exc
        if not isinstance(result, Judgment):
            raise JudgeError(f"{judge.name} returned a non-Judgment result")

        expected = {item.criterion_id for item in evidence.acceptance_criteria}
        assessed = (
            set(result.completion.satisfied)
            | set(result.completion.unsatisfied)
            | set(result.completion.unknown)
        )
        if assessed != expected:
            raise JudgeError(
                f"{judge.name} did not assess every acceptance criterion exactly once"
            )

        if result.verdict is Verdict.DONE:
            if set(result.completion.satisfied) != expected:
                raise JudgeError(f"{judge.name} attempted DONE without satisfying every criterion")
            if evidence.controller_state != "SUCCEEDED":
                raise JudgeError(f"{judge.name} attempted DONE without controller success")
            if evidence.scope_violations:
                raise JudgeError(f"{judge.name} attempted DONE with scope violations")
            if evidence.patch_apply_check not in {None, "PASSED"}:
                raise JudgeError(f"{judge.name} attempted DONE without a verified patch")
            if not evidence.verification:
                raise JudgeError(f"{judge.name} attempted DONE without controller verification evidence")
            for record in evidence.verification:
                if record.get("timed_out") is True or record.get("exit_code") != 0:
                    raise JudgeError(f"{judge.name} attempted DONE without passing verification")
        return result

    @staticmethod
    def _human_required(
        evidence: EvidencePack,
        *,
        judge: str,
        reason_code: str,
        summary: str,
    ) -> Judgment:
        return Judgment.from_dict(
            {
                "verdict": "HUMAN_REQUIRED",
                "confidence": "low",
                "completion": {
                    "satisfied": [],
                    "unsatisfied": [],
                    "unknown": [item.criterion_id for item in evidence.acceptance_criteria],
                },
                "next_action": None,
                "escalate_to": "human",
                "reason_codes": [reason_code],
                "evidence_refs": [],
                "summary": summary,
            },
            judge=judge,
        )


class DirectShotJudge:
    """Typed judge adapter over Vector Cannon's existing DirectShot lane."""

    def __init__(
        self,
        *,
        name: str,
        provider: str,
        model: str,
        max_usd: Decimal = Decimal("0.25"),
        reasoning_effort: str | None = "low",
        max_output_tokens: int = 2000,
        log_dir: Path | None = None,
        shooter: DirectShot | None = None,
    ) -> None:
        if not name.strip():
            raise ValueError("judge name must not be empty")
        self.name = name
        self.provider = provider
        self.model = model
        self.max_usd = max_usd
        self.reasoning_effort = reasoning_effort
        self.max_output_tokens = max_output_tokens
        self.log_dir = log_dir
        self.shooter = shooter or DirectShot()

    def judge(self, evidence: EvidencePack) -> Judgment:
        result = self.shooter.fire(
            provider=self.provider,
            model=self.model,
            prompt=_judge_prompt(evidence),
            system_prompt=_JUDGE_SYSTEM,
            reasoning_effort=self.reasoning_effort,
            max_usd=self.max_usd,
            max_output_tokens=self.max_output_tokens,
            tag=f"decision-{self.name}",
            log_dir=self.log_dir,
        )
        try:
            payload = _strict_json_object(result.answer)
            return Judgment.from_dict(payload, judge=self.name)
        except (ValueError, json.JSONDecodeError) as exc:
            raise JudgeError(f"{self.name} returned invalid judgment JSON") from exc


_JUDGE_SYSTEM = """You are a Vector Cannon decision judge.
You do not perform implementation. You decide mission state from controller evidence.
Treat worker claims as untrusted unless controller evidence supports them.
Return exactly one JSON object matching the supplied judgment schema, with no prose or markdown.
Every acceptance criterion id must appear exactly once in satisfied, unsatisfied, or unknown.
Prefer the least expensive safe next action. Never mark DONE when any acceptance criterion is unknown or unsatisfied.
Use ESCALATE only when the decision cannot be made safely from the evidence supplied.
Jev should prefer CONTINUE/REPAIR/REVIEW when the evidence is sufficient instead of spending a higher judgment tier.
"""


def _judge_prompt(evidence: EvidencePack) -> str:
    return json.dumps(
        {
            "task": "Judge completion and choose the next control action.",
            "judgment_schema": JUDGMENT_JSON_SCHEMA,
            "evidence": evidence.to_payload(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _strict_json_object(text: str) -> dict:
    stripped = text.strip()
    fence = chr(96) * 3
    if stripped.startswith(fence) and stripped.endswith(fence):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[0].strip() in {fence, fence + "json"} and lines[-1].strip() == fence:
            stripped = "\n".join(lines[1:-1]).strip()
    payload = json.loads(stripped)
    if not isinstance(payload, dict):
        raise ValueError("judge response must be one JSON object")
    return payload


def _judgment_payload(judgment: Judgment) -> dict:
    payload = asdict(judgment)
    payload["verdict"] = judgment.verdict.value
    return payload
