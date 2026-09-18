from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from .decision_gate import DecisionGate, DecisionTrace, JudgeError
from .evidence import AcceptanceCriterion, EvidencePack
from .judgment import Judgment, Verdict
from .repo_launcher import RepositorySession


class AdaptiveState(str, Enum):
    DONE = "DONE"
    BLOCKED = "BLOCKED"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"
    SHOT_LIMIT = "SHOT_LIMIT"


@dataclass(frozen=True)
class JobSpec:
    job_id: str
    mission: str
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    allowed_files: tuple[str, ...]
    verification_commands: tuple[tuple[str, ...], ...]
    max_shots: int = 6
    timeout_seconds: float = 120.0
    max_sol_judgments: int = 2
    max_astra_judgments: int = 1

    def __post_init__(self) -> None:
        if not self.job_id.strip() or not self.mission.strip():
            raise ValueError("job_id and mission must not be empty")
        if not self.acceptance_criteria:
            raise ValueError("job requires acceptance criteria")
        criterion_ids = [item.criterion_id for item in self.acceptance_criteria]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("acceptance criterion ids must be unique")
        if not self.allowed_files or any(not item.strip() for item in self.allowed_files):
            raise ValueError("job requires non-empty allowed_files")
        if len(self.allowed_files) != len(set(self.allowed_files)):
            raise ValueError("allowed_files must be unique")
        if not self.verification_commands:
            raise ValueError("job requires verification commands")
        if any(not argv or any(not item for item in argv) for argv in self.verification_commands):
            raise ValueError("verification commands must contain non-empty argv arrays")
        if not 1 <= self.max_shots <= 100:
            raise ValueError("max_shots must be between 1 and 100")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not 0 <= self.max_sol_judgments <= 100:
            raise ValueError("max_sol_judgments must be between 0 and 100")
        if not 0 <= self.max_astra_judgments <= 100:
            raise ValueError("max_astra_judgments must be between 0 and 100")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobSpec":
        if not isinstance(data, dict):
            raise ValueError("job spec must be an object")
        criteria_raw = data.get("acceptance_criteria")
        if not isinstance(criteria_raw, list) or not criteria_raw:
            raise ValueError("acceptance_criteria must be a non-empty array")
        criteria: list[AcceptanceCriterion] = []
        for item in criteria_raw:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("id"), str)
                or not isinstance(item.get("description"), str)
            ):
                raise ValueError("acceptance criteria require id and description strings")
            criteria.append(AcceptanceCriterion(item["id"], item["description"]))

        allowed = data.get("allowed_files")
        if not isinstance(allowed, list) or any(not isinstance(item, str) for item in allowed):
            raise ValueError("allowed_files must be an array of strings")

        commands_raw = data.get("verification_commands")
        if not isinstance(commands_raw, list) or not commands_raw:
            raise ValueError("verification_commands must be a non-empty argv array list")
        commands: list[tuple[str, ...]] = []
        for argv in commands_raw:
            if (
                not isinstance(argv, list)
                or not argv
                or any(not isinstance(item, str) or not item for item in argv)
            ):
                raise ValueError("verification commands must be non-empty string arrays")
            commands.append(tuple(argv))

        return cls(
            job_id=str(data.get("job_id") or ""),
            mission=str(data.get("mission") or ""),
            acceptance_criteria=tuple(criteria),
            allowed_files=tuple(allowed),
            verification_commands=tuple(commands),
            max_shots=int(data.get("max_shots", 6)),
            timeout_seconds=float(data.get("timeout_seconds", 120.0)),
            max_sol_judgments=int(data.get("max_sol_judgments", 2)),
            max_astra_judgments=int(data.get("max_astra_judgments", 1)),
        )


@dataclass(frozen=True)
class ShotDirective:
    task_id: str
    instructions: str
    allowed_files: tuple[str, ...]
    verification_commands: tuple[tuple[str, ...], ...]
    timeout_seconds: float
    source_verdict: str

    def __post_init__(self) -> None:
        if not self.task_id.strip() or not self.instructions.strip():
            raise ValueError("task_id and instructions must not be empty")
        if not self.allowed_files:
            raise ValueError("shot directive requires at least one allowed file")


class ShotExecutor(Protocol):
    def execute(self, directive: ShotDirective) -> dict[str, Any]:
        ...


class RepositoryShotExecutor:
    """Adaptive ShotExecutor backed by an isolated RepositorySession."""

    def __init__(self, session: RepositorySession, *, execute: bool = True) -> None:
        self.session = session
        self.execute_live = execute

    @classmethod
    def create(
        cls,
        *,
        source_repo: Path,
        session_dir: Path,
        codex_path: str = "codex",
        execute: bool = True,
    ) -> "RepositoryShotExecutor":
        return cls(
            RepositorySession(
                source_repo=source_repo,
                session_dir=session_dir,
                codex_path=codex_path,
            ),
            execute=execute,
        )

    def execute(self, directive: ShotDirective) -> dict[str, Any]:
        return self.session.execute(
            task_id=directive.task_id,
            instructions=directive.instructions,
            allowed_files=directive.allowed_files,
            verification_commands=directive.verification_commands,
            timeout_seconds=directive.timeout_seconds,
            execute=self.execute_live,
        )

    def write_cumulative_patch(self, path: Path) -> Path:
        return self.session.write_cumulative_patch(path)


class ShotPlanner(Protocol):
    def initial(self, job: JobSpec) -> ShotDirective:
        ...

    def next(self, job: JobSpec, judgment: Judgment, *, shot_number: int) -> ShotDirective:
        ...


class JudgmentPlanner:
    """Deterministic bridge from typed judgment to the next bounded shot."""

    def initial(self, job: JobSpec) -> ShotDirective:
        return ShotDirective(
            task_id=f"{job.job_id}-s001",
            instructions=job.mission,
            allowed_files=job.allowed_files,
            verification_commands=job.verification_commands,
            timeout_seconds=job.timeout_seconds,
            source_verdict="INITIAL",
        )

    def next(self, job: JobSpec, judgment: Judgment, *, shot_number: int) -> ShotDirective:
        if judgment.verdict not in {Verdict.CONTINUE, Verdict.REPAIR, Verdict.REVIEW}:
            raise ValueError("only CONTINUE, REPAIR, or REVIEW may create another shot")
        action = judgment.next_action
        if action is None:
            raise ValueError("next-action verdict is missing next_action")

        requested_scope = tuple(action.allowed_scope)
        if requested_scope:
            outside = sorted(set(requested_scope) - set(job.allowed_files))
            if outside:
                raise ValueError(f"judge attempted to expand job scope: {outside}")
            scope = requested_scope
        else:
            scope = job.allowed_files

        return ShotDirective(
            task_id=f"{job.job_id}-s{shot_number:03d}",
            instructions=f"{action.kind}: {action.goal}",
            allowed_files=scope,
            verification_commands=job.verification_commands,
            timeout_seconds=job.timeout_seconds,
            source_verdict=judgment.verdict.value,
        )


@dataclass(frozen=True)
class AdaptiveStep:
    shot_number: int
    directive: ShotDirective
    result: dict[str, Any]
    evidence: EvidencePack
    decision: DecisionTrace


@dataclass(frozen=True)
class AdaptiveRun:
    state: AdaptiveState
    steps: tuple[AdaptiveStep, ...]
    final_judgment: Judgment | None
    stop_reason: str
    attempted_shots: int

    @property
    def shots_fired(self) -> int:
        return self.attempted_shots

    @property
    def judge_calls(self) -> int:
        return sum(
            1
            for step in self.steps
            for item in step.decision.judgments
            if item.judge in {"jev", "sol", "astra"}
        )

    @property
    def jev_judgments(self) -> int:
        return sum(step.decision.count("jev") for step in self.steps)

    @property
    def sol_judgments(self) -> int:
        return sum(step.decision.count("sol") for step in self.steps)

    @property
    def astra_judgments(self) -> int:
        return sum(step.decision.count("astra") for step in self.steps)

    def to_payload(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "shots_fired": self.shots_fired,
            "judge_calls": self.judge_calls,
            "judge_counts": {
                "jev": self.jev_judgments,
                "sol": self.sol_judgments,
                "astra": self.astra_judgments,
            },
            "stop_reason": self.stop_reason,
            "final_judgment": (
                self.steps[-1].decision.to_payload()["final"] if self.steps else None
            ),
            "steps": [
                {
                    "shot_number": step.shot_number,
                    "directive": asdict(step.directive),
                    "result": step.result,
                    "decision": step.decision.to_payload(),
                }
                for step in self.steps
            ],
        }


class AdaptiveOrchestrator:
    """Run bounded shots until typed judgment reaches DONE or a hard stop."""

    def __init__(
        self,
        *,
        decision_gate: DecisionGate,
        executor: ShotExecutor,
        planner: ShotPlanner | None = None,
    ) -> None:
        self.decision_gate = decision_gate
        self.executor = executor
        self.planner = planner or JudgmentPlanner()

    def run(self, job: JobSpec) -> AdaptiveRun:
        steps: list[AdaptiveStep] = []
        prior: list[dict[str, Any]] = []
        directive = self.planner.initial(job)
        sol_used = 0
        astra_used = 0

        for shot_number in range(1, job.max_shots + 1):
            result = self.executor.execute(directive)
            if not isinstance(result, dict):
                return AdaptiveRun(
                    AdaptiveState.BLOCKED,
                    tuple(steps),
                    steps[-1].decision.final if steps else None,
                    "EXECUTOR_RETURNED_INVALID_RESULT",
                    shot_number,
                )
            evidence = EvidencePack.from_launcher_result(
                job_id=job.job_id,
                mission=job.mission,
                acceptance_criteria=job.acceptance_criteria,
                result=result,
                prior_judgments=prior,
            )
            try:
                decision = self.decision_gate.decide(
                    evidence,
                    allow_sol=sol_used < job.max_sol_judgments,
                    allow_astra=astra_used < job.max_astra_judgments,
                )
            except JudgeError:
                return AdaptiveRun(
                    AdaptiveState.BLOCKED,
                    tuple(steps),
                    steps[-1].decision.final if steps else None,
                    "DECISION_GATE_FAILED_CLOSED",
                    shot_number,
                )

            sol_used += decision.count("sol")
            astra_used += decision.count("astra")

            step = AdaptiveStep(
                shot_number=shot_number,
                directive=directive,
                result=result,
                evidence=evidence,
                decision=decision,
            )
            steps.append(step)
            prior.append(decision.to_payload()["final"])
            final = decision.final

            if final.verdict is Verdict.DONE:
                return AdaptiveRun(
                    AdaptiveState.DONE,
                    tuple(steps),
                    final,
                    "ACCEPTANCE_CRITERIA_SATISFIED",
                    shot_number,
                )
            if final.verdict is Verdict.HUMAN_REQUIRED or (
                final.verdict is Verdict.ESCALATE and final.escalate_to == "human"
            ):
                return AdaptiveRun(
                    AdaptiveState.HUMAN_REQUIRED,
                    tuple(steps),
                    final,
                    "HUMAN_DECISION_REQUIRED",
                    shot_number,
                )
            if final.verdict is Verdict.BLOCKED:
                return AdaptiveRun(
                    AdaptiveState.BLOCKED,
                    tuple(steps),
                    final,
                    "JUDGE_REPORTED_BLOCKED",
                    shot_number,
                )
            if final.verdict is Verdict.ESCALATE:
                return AdaptiveRun(
                    AdaptiveState.BLOCKED,
                    tuple(steps),
                    final,
                    "AI_ESCALATION_EXHAUSTED",
                    shot_number,
                )

            if shot_number >= job.max_shots:
                return AdaptiveRun(
                    AdaptiveState.SHOT_LIMIT,
                    tuple(steps),
                    final,
                    "MAX_SHOTS_REACHED",
                    shot_number,
                )
            try:
                directive = self.planner.next(job, final, shot_number=shot_number + 1)
            except ValueError:
                return AdaptiveRun(
                    AdaptiveState.BLOCKED,
                    tuple(steps),
                    final,
                    "NEXT_SHOT_PLAN_REJECTED",
                    shot_number,
                )

        raise AssertionError("adaptive loop exhausted unexpectedly")
