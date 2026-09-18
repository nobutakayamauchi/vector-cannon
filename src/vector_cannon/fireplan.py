from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .direct import DirectShot, DirectShotResult
from .venue import EnergyLineRouter, RouteDecision


@dataclass(frozen=True)
class Warhead:
    target: str
    role: str = "general"
    system_prompt: str | None = None


@dataclass(frozen=True)
class Charge:
    level: int
    reasoning_effort: str | None
    max_output_tokens: int
    critique_rounds: int = 0

    @classmethod
    def preset(cls, level: int) -> "Charge":
        presets = {
            1: cls(1, "low", 1500, 0),
            2: cls(2, "medium", 3000, 0),
            3: cls(3, "high", 6000, 1),
            4: cls(4, "high", 10000, 2),
            5: cls(5, "max", 16000, 3),
        }
        try:
            return presets[level]
        except KeyError as exc:
            raise ValueError("charge level must be 1..5") from exc


@dataclass(frozen=True)
class FirePlan:
    mission: str
    warhead: Warhead
    charge: Charge
    route: RouteDecision
    max_usd: Decimal

    def render(self) -> str:
        winner = self.route.winner
        estimated = "unknown" if winner.estimated_cost is None else f"${winner.estimated_cost}"
        return "\n".join(
            [
                "MISSION LOAD",
                f"TARGET -> {self.warhead.target}",
                f"ROLE -> {self.warhead.role}",
                f"ENERGY LINE -> {winner.provider}/{winner.model_id}",
                f"CHARGE -> {self.charge.level} ({self.charge.reasoning_effort or 'none'})",
                f"MAX OUTPUT -> {self.charge.max_output_tokens}",
                f"BUDGET LOCK -> ${self.max_usd}",
                f"ESTIMATED TOTAL -> {estimated}",
                "READY TO FIRE",
            ]
        )


class FireControl:
    def __init__(self, router: EnergyLineRouter | None = None, shooter: DirectShot | None = None) -> None:
        self.router = router or EnergyLineRouter()
        self.shooter = shooter or DirectShot()

    def plan(
        self,
        *,
        mission: str,
        target: str,
        goal: str = "balanced",
        max_usd: Decimal = Decimal("1.00"),
        charge_level: int = 2,
        role: str = "general",
        system_prompt: str | None = None,
    ) -> FirePlan:
        if not mission.strip():
            raise ValueError("mission must not be empty")
        charge = Charge.preset(charge_level)
        route = self.router.choose(
            target,
            goal=goal,
            max_usd=max_usd,
            max_output_tokens=charge.max_output_tokens,
        )
        return FirePlan(
            mission=mission,
            warhead=Warhead(target=target, role=role, system_prompt=system_prompt),
            charge=charge,
            route=route,
            max_usd=max_usd,
        )

    def fire(self, plan: FirePlan, *, log_dir=None, tag: str = "auto-shot") -> DirectShotResult:
        winner = plan.route.winner
        input_price = None
        output_price = None
        if winner.input_price_per_token is not None and winner.output_price_per_token is not None:
            input_price = winner.input_price_per_token * Decimal("1000000")
            output_price = winner.output_price_per_token * Decimal("1000000")
        return self.shooter.fire(
            provider=winner.provider,
            model=winner.model_id,
            prompt=plan.mission,
            system_prompt=plan.warhead.system_prompt,
            reasoning_effort=plan.charge.reasoning_effort,
            max_usd=plan.max_usd,
            max_output_tokens=plan.charge.max_output_tokens,
            input_price_per_million=input_price,
            output_price_per_million=output_price,
            tag=tag,
            log_dir=log_dir,
        )
