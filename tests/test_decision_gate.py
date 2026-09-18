from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from vector_cannon.decision_gate import DecisionGate, DirectShotJudge, JudgeError
from vector_cannon.evidence import AcceptanceCriterion, EvidencePack
from vector_cannon.judgment import Judgment, Verdict


CRITERIA = (
    AcceptanceCriterion("AC-001", "requested file exists"),
    AcceptanceCriterion("AC-002", "controller verification passes"),
)


def evidence() -> EvidencePack:
    return EvidencePack.from_launcher_result(
        job_id="job-1",
        mission="complete the fixture",
        acceptance_criteria=CRITERIA,
        result={
            "task_id": "shot-1",
            "state": "SUCCEEDED",
            "stop_reason": "PATCH_AND_CONTROLLER_VERIFICATION_PASSED",
            "changed_files": ["fixture-output.txt"],
            "verification": [{"exit_code": 0, "timed_out": False}],
            "worker_claim": {"status": "completed", "summary": "done"},
            "usage": {"status": "UNKNOWN"},
            "artifacts": {"patch": "/tmp/changes.patch"},
        },
    )


def payload(
    verdict: str,
    *,
    confidence: str = "high",
    satisfied: list[str] | None = None,
    unsatisfied: list[str] | None = None,
    unknown: list[str] | None = None,
    action: dict | None = None,
    escalate_to: str | None = None,
    reason_codes: list[str] | None = None,
) -> dict:
    return {
        "verdict": verdict,
        "confidence": confidence,
        "completion": {
            "satisfied": satisfied or [],
            "unsatisfied": unsatisfied or [],
            "unknown": unknown or [],
        },
        "next_action": action,
        "escalate_to": escalate_to,
        "reason_codes": reason_codes or [],
        "evidence_refs": ["controller:verification"],
        "summary": f"{verdict} judgment",
    }


@dataclass
class FakeJudge:
    name: str
    result: Judgment | Exception
    calls: int = 0

    def judge(self, evidence: EvidencePack) -> Judgment:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def judgment(name: str, data: dict) -> Judgment:
    return Judgment.from_dict(data, judge=name)


def test_done_requires_all_acceptance_evidence() -> None:
    with pytest.raises(ValueError):
        judgment(
            "jev",
            payload("DONE", satisfied=["AC-001"], unknown=["AC-002"]),
        )


def test_jev_done_stops_without_sol_or_astra() -> None:
    jev = FakeJudge("jev", judgment("jev", payload("DONE", satisfied=["AC-001", "AC-002"])))
    sol = FakeJudge("sol", judgment("sol", payload("DONE", satisfied=["AC-001", "AC-002"])))
    astra = FakeJudge("astra", judgment("astra", payload("DONE", satisfied=["AC-001", "AC-002"])))

    trace = DecisionGate(jev, sol=sol, astra=astra).decide(evidence())

    assert trace.final.verdict is Verdict.DONE
    assert trace.judges_used == ("jev",)
    assert trace.escalated is False
    assert jev.calls == 1 and sol.calls == 0 and astra.calls == 0


def test_jev_repair_stops_without_expensive_escalation() -> None:
    repair = payload(
        "REPAIR",
        confidence="low",
        satisfied=["AC-001"],
        unsatisfied=["AC-002"],
        action={"kind": "REPAIR", "goal": "fix AC-002", "allowed_scope": ["tests/test_x.py"]},
    )
    jev = FakeJudge("jev", judgment("jev", repair))
    sol = FakeJudge("sol", judgment("sol", payload("DONE", satisfied=["AC-001", "AC-002"])))
    astra = FakeJudge("astra", judgment("astra", payload("DONE", satisfied=["AC-001", "AC-002"])))

    trace = DecisionGate(jev, sol=sol, astra=astra).decide(evidence())

    assert trace.final.verdict is Verdict.REPAIR
    assert trace.judges_used == ("jev",)
    assert sol.calls == 0 and astra.calls == 0


def test_jev_escalation_calls_sol_but_not_astra_when_sol_resolves() -> None:
    jev = FakeJudge(
        "jev",
        judgment(
            "jev",
            payload(
                "ESCALATE",
                confidence="low",
                unknown=["AC-001", "AC-002"],
                escalate_to="sol",
                reason_codes=["AMBIGUOUS_ARCHITECTURE"],
            ),
        ),
    )
    sol = FakeJudge(
        "sol",
        judgment(
            "sol",
            payload(
                "REVIEW",
                satisfied=["AC-001"],
                unknown=["AC-002"],
                action={"kind": "REVIEW", "goal": "inspect AC-002", "allowed_scope": []},
            ),
        ),
    )
    astra = FakeJudge("astra", judgment("astra", payload("DONE", satisfied=["AC-001", "AC-002"])))

    trace = DecisionGate(jev, sol=sol, astra=astra).decide(evidence())

    assert trace.judges_used == ("jev", "sol")
    assert trace.final.verdict is Verdict.REVIEW
    assert astra.calls == 0


def test_invalid_jev_output_fails_closed_into_sol() -> None:
    jev = FakeJudge("jev", JudgeError("invalid typed output"))
    sol = FakeJudge("sol", judgment("sol", payload("DONE", satisfied=["AC-001", "AC-002"])))

    trace = DecisionGate(jev, sol=sol).decide(evidence())

    assert trace.judges_used == ("jev", "sol")
    assert trace.judgments[0].verdict is Verdict.ESCALATE
    assert "JUDGE_OUTPUT_INVALID" in trace.judgments[0].reason_codes
    assert trace.final.verdict is Verdict.DONE


def test_sol_escalation_calls_astra_only_at_last_resort() -> None:
    jev = FakeJudge(
        "jev",
        judgment("jev", payload("ESCALATE", unknown=["AC-001", "AC-002"], escalate_to="sol")),
    )
    sol = FakeJudge(
        "sol",
        judgment("sol", payload("ESCALATE", satisfied=["AC-001"], unknown=["AC-002"], escalate_to="astra")),
    )
    astra = FakeJudge(
        "astra",
        judgment(
            "astra",
            payload(
                "REPAIR",
                satisfied=["AC-001"],
                unsatisfied=["AC-002"],
                action={"kind": "REPAIR", "goal": "repair AC-002", "allowed_scope": ["src/x.py"]},
            ),
        ),
    )

    trace = DecisionGate(jev, sol=sol, astra=astra).decide(evidence())

    assert trace.judges_used == ("jev", "sol", "astra")
    assert trace.final.verdict is Verdict.REPAIR
    assert astra.calls == 1


def test_human_escalation_does_not_consume_sol_or_astra() -> None:
    jev = FakeJudge(
        "jev",
        judgment(
            "jev",
            payload(
                "ESCALATE",
                confidence="medium",
                satisfied=["AC-001"],
                unknown=["AC-002"],
                escalate_to="human",
                reason_codes=["OPERATOR_DECISION_REQUIRED"],
            ),
        ),
    )
    sol = FakeJudge("sol", judgment("sol", payload("DONE", satisfied=["AC-001", "AC-002"])))
    astra = FakeJudge("astra", judgment("astra", payload("DONE", satisfied=["AC-001", "AC-002"])))

    trace = DecisionGate(jev, sol=sol, astra=astra).decide(evidence())

    assert trace.judges_used == ("jev",)
    assert trace.final.escalate_to == "human"
    assert sol.calls == 0 and astra.calls == 0


def test_evidence_pack_uses_controller_facts_and_keeps_worker_claim_separate() -> None:
    pack = evidence()
    body = pack.to_payload()

    assert body["controller"]["state"] == "SUCCEEDED"
    assert body["controller"]["verification"][0]["exit_code"] == 0
    assert body["worker_claim"]["status"] == "completed"
    assert body["acceptance_criteria"][0]["criterion_id"] == "AC-001"


class FakeShooter:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls = []

    def fire(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(answer=self.answer)


def test_direct_shot_judge_parses_typed_json() -> None:
    raw = payload("DONE", satisfied=["AC-001", "AC-002"])
    shooter = FakeShooter(__import__("json").dumps(raw))
    judge = DirectShotJudge(
        name="jev",
        provider="vercel",
        model="demo/jev",
        shooter=shooter,
    )

    result = judge.judge(evidence())

    assert result.verdict is Verdict.DONE
    assert result.judge == "jev"
    assert shooter.calls[0]["tag"] == "decision-jev"


def test_direct_shot_judge_rejects_prose_wrapped_json() -> None:
    shooter = FakeShooter("Here is the result: {}")
    judge = DirectShotJudge(
        name="jev",
        provider="vercel",
        model="demo/jev",
        shooter=shooter,
    )

    with pytest.raises(JudgeError):
        judge.judge(evidence())



def test_missing_acceptance_criterion_fails_closed() -> None:
    jev = FakeJudge(
        "jev",
        judgment("jev", payload("DONE", satisfied=["AC-001"])),
    )

    trace = DecisionGate(jev).decide(evidence())

    assert trace.final.verdict is Verdict.HUMAN_REQUIRED
    assert "JUDGE_OUTPUT_INVALID" in trace.final.reason_codes


def test_astra_can_be_disabled_by_mission_budget() -> None:
    jev = FakeJudge(
        "jev",
        judgment(
            "jev",
            payload(
                "ESCALATE",
                unknown=["AC-001", "AC-002"],
                escalate_to="sol",
            ),
        ),
    )
    sol = FakeJudge(
        "sol",
        judgment(
            "sol",
            payload(
                "ESCALATE",
                satisfied=["AC-001"],
                unknown=["AC-002"],
                escalate_to="astra",
            ),
        ),
    )
    astra = FakeJudge(
        "astra",
        judgment("astra", payload("DONE", satisfied=["AC-001", "AC-002"])),
    )

    trace = DecisionGate(jev, sol=sol, astra=astra).decide(
        evidence(),
        allow_astra=False,
    )

    assert trace.final.verdict is Verdict.HUMAN_REQUIRED
    assert "DECISION_BUDGET_EXHAUSTED" in trace.final.reason_codes
    assert astra.calls == 0
