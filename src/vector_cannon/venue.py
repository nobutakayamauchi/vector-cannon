from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Iterable

from .gateway import GatewayError, PROVIDER_SPECS, OpenAICompatibleGateway, make_gateway


@dataclass(frozen=True)
class VenueStatus:
    provider: str
    key_env: str
    base_url: str
    ready: bool
    exact_credit_meter: bool
    catalog_pricing: bool


@dataclass(frozen=True)
class RouteCandidate:
    provider: str
    model_id: str
    ready: bool
    input_price_per_token: Decimal | None
    output_price_per_token: Decimal | None
    estimated_cost: Decimal | None
    price_confidence: str
    ledger_average_seconds: float | None = None


@dataclass(frozen=True)
class RouteDecision:
    target: str
    goal: str
    winner: RouteCandidate
    candidates: tuple[RouteCandidate, ...]
    reason: str


class VenueRegistry:
    """Normalized registry for approved inference venues.

    Endpoints and credential variable names are inherited from gateway.PROVIDER_SPECS;
    callers cannot inject arbitrary provider URLs.
    """

    def statuses(self) -> list[VenueStatus]:
        rows: list[VenueStatus] = []
        for provider_id, spec in sorted(PROVIDER_SPECS.items()):
            rows.append(
                VenueStatus(
                    provider=provider_id,
                    key_env=spec.key_env,
                    base_url=spec.base_url,
                    ready=bool(os.getenv(spec.key_env)),
                    exact_credit_meter=spec.has_exact_credit_meter,
                    catalog_pricing=spec.catalog_pricing_per_token,
                )
            )
        return rows


class EnergyLineRouter:
    def __init__(
        self,
        *,
        gateway_factory: Callable[[str], OpenAICompatibleGateway] = make_gateway,
        ledger_latency: dict[tuple[str, str], float] | None = None,
    ) -> None:
        self.gateway_factory = gateway_factory
        self.ledger_latency = ledger_latency or {}

    @staticmethod
    def _estimate_cost(
        input_price: Decimal | None,
        output_price: Decimal | None,
        *,
        estimated_input_tokens: int,
        max_output_tokens: int,
    ) -> Decimal | None:
        if input_price is None or output_price is None:
            return None
        return Decimal(estimated_input_tokens) * input_price + Decimal(max_output_tokens) * output_price

    def discover(
        self,
        target: str,
        *,
        estimated_input_tokens: int = 1000,
        max_output_tokens: int = 2000,
        include_unarmed: bool = True,
    ) -> list[RouteCandidate]:
        needle = target.casefold().strip()
        if not needle:
            raise ValueError("target model/name must not be empty")

        candidates: list[RouteCandidate] = []
        for provider_id, spec in sorted(PROVIDER_SPECS.items()):
            ready = bool(os.getenv(spec.key_env))
            if not ready and not include_unarmed:
                continue
            gateway = self.gateway_factory(provider_id)
            try:
                models = gateway.list_models()
            except GatewayError:
                # An unarmed venue may still have a public catalog, while an armed venue can
                # be temporarily unavailable. In either case, do not invent model support.
                continue
            for model in models:
                model_id = str(model.get("id") or "")
                name = str(model.get("name") or "")
                if needle not in model_id.casefold() and needle not in name.casefold():
                    continue
                if gateway.declared_free_model(model_id):
                    input_price = output_price = Decimal("0")
                    confidence = "provider_declared_free"
                else:
                    input_price, output_price = gateway.catalog_pricing(model)
                    confidence = "provider_catalog" if input_price is not None and output_price is not None else "unknown"
                candidates.append(
                    RouteCandidate(
                        provider=provider_id,
                        model_id=model_id,
                        ready=ready,
                        input_price_per_token=input_price,
                        output_price_per_token=output_price,
                        estimated_cost=self._estimate_cost(
                            input_price,
                            output_price,
                            estimated_input_tokens=estimated_input_tokens,
                            max_output_tokens=max_output_tokens,
                        ),
                        price_confidence=confidence,
                        ledger_average_seconds=self.ledger_latency.get((provider_id, model_id)),
                    )
                )
        return candidates

    def choose(
        self,
        target: str,
        *,
        goal: str = "balanced",
        max_usd: Decimal = Decimal("1.00"),
        estimated_input_tokens: int = 1000,
        max_output_tokens: int = 2000,
    ) -> RouteDecision:
        if max_usd <= 0:
            raise ValueError("max_usd must be positive")
        if goal not in {"cheapest", "fastest", "balanced"}:
            raise ValueError("goal must be cheapest, fastest, or balanced")

        all_candidates = self.discover(
            target,
            estimated_input_tokens=estimated_input_tokens,
            max_output_tokens=max_output_tokens,
            include_unarmed=True,
        )
        eligible = [candidate for candidate in all_candidates if candidate.ready]
        eligible = [
            candidate
            for candidate in eligible
            if candidate.estimated_cost is None or candidate.estimated_cost <= max_usd
        ]
        if not eligible:
            raise GatewayError(f"No READY approved route found for '{target}' inside budget ${max_usd}.")

        def cost_key(candidate: RouteCandidate) -> tuple[int, Decimal, str, str]:
            unknown = candidate.estimated_cost is None
            return (1 if unknown else 0, candidate.estimated_cost or Decimal("Infinity"), candidate.provider, candidate.model_id)

        def speed_key(candidate: RouteCandidate) -> tuple[int, float, tuple[int, Decimal, str, str]]:
            unknown = candidate.ledger_average_seconds is None
            return (1 if unknown else 0, candidate.ledger_average_seconds or float("inf"), cost_key(candidate))

        if goal == "cheapest":
            winner = min(eligible, key=cost_key)
            reason = "lowest known estimated cost among READY approved routes"
        elif goal == "fastest":
            winner = min(eligible, key=speed_key)
            reason = "lowest known ledger latency; deterministic fallback uses cost/provider/model"
        else:
            # Balanced prefers known price, then known latency, then cost. This stays
            # deterministic until personal success evidence is added by Doctrine.
            def balanced_key(candidate: RouteCandidate):
                return (
                    1 if candidate.estimated_cost is None else 0,
                    1 if candidate.ledger_average_seconds is None else 0,
                    candidate.estimated_cost or Decimal("Infinity"),
                    candidate.ledger_average_seconds or float("inf"),
                    candidate.provider,
                    candidate.model_id,
                )

            winner = min(eligible, key=balanced_key)
            reason = "best deterministic balance of READY status, known price, known latency, and cost"

        return RouteDecision(
            target=target,
            goal=goal,
            winner=winner,
            candidates=tuple(all_candidates),
            reason=reason,
        )


def latency_index(rows: Iterable[object]) -> dict[tuple[str, str], float]:
    result: dict[tuple[str, str], float] = {}
    for row in rows:
        provider = getattr(row, "provider", None)
        model = getattr(row, "model", None)
        average = getattr(row, "average_seconds", None)
        if provider and model and average is not None:
            result[(str(provider), str(model))] = float(average)
    return result
