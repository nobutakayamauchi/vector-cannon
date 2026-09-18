from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from .direct import DirectShotResult
from .fireplan import FireControl, FirePlan


@dataclass(frozen=True)
class MissionStage:
    name: str
    target: str
    goal: str = "balanced"
    charge_level: int = 2
    role: str = "general"
    budget_usd: Decimal = Decimal("0.50")
    system_prompt: str | None = None
    consume_previous: bool = True


@dataclass(frozen=True)
class StageResult:
    stage: MissionStage
    plan: FirePlan
    shot: DirectShotResult


@dataclass(frozen=True)
class MissionResult:
    stages: tuple[StageResult, ...]

    @property
    def total_cost(self) -> Decimal:
        return sum((Decimal(item.shot.observed_cost) for item in self.stages), Decimal("0"))

    @property
    def final_answer(self) -> str:
        return self.stages[-1].shot.answer if self.stages else ""


class Orchestrator:
    """Executes a bounded, explicit multi-stage inference graph.

    Every stage is separately routed and budget-locked. Previous stage output is passed
    only when the stage explicitly opts in via consume_previous.
    """

    def __init__(self, fire_control: FireControl | None = None) -> None:
        self.fire_control = fire_control or FireControl()

    def run(
        self,
        *,
        mission: str,
        stages: Iterable[MissionStage],
        max_total_usd: Decimal,
        log_dir=None,
        tag: str = "mission",
    ) -> MissionResult:
        if max_total_usd <= 0:
            raise ValueError("max_total_usd must be positive")
        stage_list = list(stages)
        if not stage_list:
            raise ValueError("at least one mission stage is required")

        allocated = sum((stage.budget_usd for stage in stage_list), Decimal("0"))
        if allocated > max_total_usd:
            raise ValueError("sum of stage budgets exceeds max_total_usd")

        results: list[StageResult] = []
        previous = ""
        observed_total = Decimal("0")
        for index, stage in enumerate(stage_list, start=1):
            if observed_total >= max_total_usd:
                break
            remaining = max_total_usd - observed_total
            budget = min(stage.budget_usd, remaining)
            stage_mission = mission
            if stage.consume_previous and previous:
                stage_mission = (
                    f"{mission}\n\nPrevious stage output ({results[-1].stage.name}):\n{previous}"
                )
            plan = self.fire_control.plan(
                mission=stage_mission,
                target=stage.target,
                goal=stage.goal,
                max_usd=budget,
                charge_level=stage.charge_level,
                role=stage.role,
                system_prompt=stage.system_prompt,
            )
            shot = self.fire_control.fire(plan, log_dir=log_dir, tag=f"{tag}-s{index}-{stage.name}")
            results.append(StageResult(stage=stage, plan=plan, shot=shot))
            previous = shot.answer
            observed_total += Decimal(shot.observed_cost)

        return MissionResult(stages=tuple(results))
