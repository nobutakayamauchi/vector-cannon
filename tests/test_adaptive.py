from __future__ import annotations

from dataclasses import dataclass, field

from vector_cannon.adaptive import (
    AdaptiveOrchestrator,
    AdaptiveState,
    JobSpec,
    ShotDirective,
)
from vector_cannon.decision_gate import DecisionGate
from vector_cannon.evidence import AcceptanceCriterion, EvidencePack
from vector_cannon.judgment import Judgment, Verdict


def jp(
    verdict: str,
    *,
    satisfied=(),
    unsatisfied=(),
    unknown=(),
    action=None,
    escalate_to=None,
    confidence="high",
):
    return {
        "verdict": verdict,
        "confidence": confidence,
        "completion": {
            "satisfied": list(satisfied),
            "unsatisfied": list(unsatisfied),
            "unknown": list(unknown),
        },
        "next_action": action,
        "escalate_to": escalate_to,
        "reason_codes": [],
        "evidence_refs": ["controller:result"],
        "summary": verdict,
    }


@dataclass
class QueueJudge:
    name: str
    queue: list[Judgment]
    calls: int = 0

    def judge(self, evidence: EvidencePack) -> Judgment:
        self.calls += 1
        return self.queue.pop(0)


@dataclass
class QueueExecutor:
    results: list[dict]
    directives: list[ShotDirective] = field(default_factory=list)

    def execute(self, directive: ShotDirective) -> dict:
        self.directives.append(directive)
        return self.results.pop(0)


def launcher_result(task_id: str, *, state="SUCCEEDED") -> dict:
    return {
        "task_id": task_id,
        "state": state,
        "stop_reason": (
            "PATCH_AND_CONTROLLER_VERIFICATION_PASSED"
            if state == "SUCCEEDED"
            else "VERIFICATION_FAILED"
        ),
        "changed_files": ["fixture-output.txt"],
        "verification": [
            {"exit_code": 0 if state == "SUCCEEDED" else 1, "timed_out": False}
        ],
        "worker_claim": {"status": "completed", "summary": "worker says done"},
        "usage": {"status": "UNKNOWN"},
        "artifacts": {"patch": "/tmp/changes.patch"},
    }


def job(*, max_shots=4) -> JobSpec:
    return JobSpec(
        job_id="job-1",
        mission="complete the fixture",
        acceptance_criteria=(
            AcceptanceCriterion("AC-001", "file is correct"),
            AcceptanceCriterion("AC-002", "verification passes"),
        ),
        allowed_files=("fixture-output.txt",),
        verification_commands=(("python3", "-m", "pytest", "-q"),),
        max_shots=max_shots,
    )


def test_adaptive_loop_repair_then_done() -> None:
    jev = QueueJudge(
        "jev",
        [
            Judgment.from_dict(
                jp(
                    "REPAIR",
                    satisfied=("AC-001",),
                    unsatisfied=("AC-002",),
                    action={
                        "kind": "REPAIR",
                        "goal": "fix verification",
                        "allowed_scope": ["fixture-output.txt"],
                    },
                    confidence="low",
                ),
                judge="jev",
            ),
            Judgment.from_dict(
                jp("DONE", satisfied=("AC-001", "AC-002")),
                judge="jev",
            ),
        ],
    )
    executor = QueueExecutor(
        [
            launcher_result("job-1-s001"),
            launcher_result("job-1-s002"),
        ]
    )

    run = AdaptiveOrchestrator(
        decision_gate=DecisionGate(jev),
        executor=executor,
    ).run(job())

    assert run.state is AdaptiveState.DONE
    assert run.shots_fired == 2
    assert run.judge_calls == 2
    assert executor.directives[1].source_verdict == "REPAIR"
    assert executor.directives[1].instructions == "REPAIR: fix verification"
    assert executor.directives[1].allowed_files == ("fixture-output.txt",)


def test_adaptive_loop_rejects_judge_scope_expansion() -> None:
    jev = QueueJudge(
        "jev",
        [
            Judgment.from_dict(
                jp(
                    "REPAIR",
                    unsatisfied=("AC-002",),
                    action={
                        "kind": "REPAIR",
                        "goal": "edit outside scope",
                        "allowed_scope": ["forbidden.py"],
                    },
                ),
                judge="jev",
            )
        ],
    )
    executor = QueueExecutor([launcher_result("job-1-s001")])

    run = AdaptiveOrchestrator(
        decision_gate=DecisionGate(jev),
        executor=executor,
    ).run(job())

    assert run.state is AdaptiveState.BLOCKED
    assert run.stop_reason == "NEXT_SHOT_PLAN_REJECTED"
    assert run.shots_fired == 1


def test_adaptive_loop_stops_at_max_shots() -> None:
    jev = QueueJudge(
        "jev",
        [
            Judgment.from_dict(
                jp(
                    "CONTINUE",
                    unknown=("AC-002",),
                    action={
                        "kind": "CONTINUE",
                        "goal": "collect more evidence",
                        "allowed_scope": ["fixture-output.txt"],
                    },
                ),
                judge="jev",
            )
        ],
    )
    executor = QueueExecutor([launcher_result("job-1-s001")])

    run = AdaptiveOrchestrator(
        decision_gate=DecisionGate(jev),
        executor=executor,
    ).run(job(max_shots=1))

    assert run.state is AdaptiveState.SHOT_LIMIT
    assert run.stop_reason == "MAX_SHOTS_REACHED"
    assert run.shots_fired == 1


def test_adaptive_loop_stops_for_human_decision() -> None:
    jev = QueueJudge(
        "jev",
        [
            Judgment.from_dict(
                jp(
                    "ESCALATE",
                    unknown=("AC-002",),
                    escalate_to="human",
                    confidence="medium",
                ),
                judge="jev",
            )
        ],
    )
    executor = QueueExecutor([launcher_result("job-1-s001")])

    run = AdaptiveOrchestrator(
        decision_gate=DecisionGate(jev),
        executor=executor,
    ).run(job())

    assert run.state is AdaptiveState.HUMAN_REQUIRED
    assert run.stop_reason == "HUMAN_DECISION_REQUIRED"
    assert run.shots_fired == 1


def test_adaptive_loop_fails_closed_when_done_lacks_controller_success() -> None:
    jev = QueueJudge(
        "jev",
        [
            Judgment.from_dict(
                jp("DONE", satisfied=("AC-001", "AC-002")),
                judge="jev",
            )
        ],
    )
    executor = QueueExecutor([launcher_result("job-1-s001", state="FAILED")])

    run = AdaptiveOrchestrator(
        decision_gate=DecisionGate(jev),
        executor=executor,
    ).run(job())

    assert run.state is AdaptiveState.BLOCKED
    assert run.stop_reason == "DECISION_GATE_FAILED_CLOSED"
    assert run.shots_fired == 1
