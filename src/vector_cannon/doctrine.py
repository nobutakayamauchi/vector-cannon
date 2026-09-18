from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from .ledger import LedgerRow


@dataclass(frozen=True)
class DoctrineRecommendation:
    provider: str
    model: str
    suggested_charge_level: int
    confidence: str
    reason: str
    average_cost: Decimal
    average_seconds: float
    failure_rate: float


class DoctrineAdvisor:
    """Small evidence-first advisor built only from observed firing history.

    It intentionally avoids pretending to know quality before the operator records
    explicit task outcomes. v0.6 therefore optimizes reliability/cost/latency only.
    """

    def recommend(
        self,
        rows: Iterable[LedgerRow],
        *,
        objective: str = "balanced",
        model_contains: str = "",
    ) -> DoctrineRecommendation:
        if objective not in {"cheapest", "fastest", "reliable", "balanced"}:
            raise ValueError("objective must be cheapest, fastest, reliable, or balanced")
        needle = model_contains.casefold().strip()
        candidates = [
            row for row in rows
            if not needle or needle in row.model.casefold()
        ]
        if not candidates:
            raise ValueError("no ledger evidence matches the requested doctrine query")

        def stats(row: LedgerRow):
            avg_cost = row.total_cost / Decimal(row.shots) if row.shots else Decimal("0")
            failure_rate = row.failures / row.shots if row.shots else 1.0
            return avg_cost, row.average_seconds, failure_rate

        if objective == "cheapest":
            winner = min(candidates, key=lambda row: (stats(row)[0], stats(row)[2], row.provider, row.model))
        elif objective == "fastest":
            winner = min(candidates, key=lambda row: (stats(row)[1], stats(row)[2], stats(row)[0], row.provider, row.model))
        elif objective == "reliable":
            winner = min(candidates, key=lambda row: (stats(row)[2], stats(row)[0], stats(row)[1], row.provider, row.model))
        else:
            # Evidence-first deterministic balance. Failure rate dominates, then cost,
            # then latency. Later versions can add operator-rated task success/ROI.
            winner = min(candidates, key=lambda row: (stats(row)[2], stats(row)[0], stats(row)[1], row.provider, row.model))

        avg_cost, avg_seconds, failure_rate = stats(winner)
        sample = winner.shots
        confidence = "low" if sample < 3 else "medium" if sample < 10 else "high"
        if failure_rate >= 0.34:
            charge = 4
        elif failure_rate >= 0.10:
            charge = 3
        else:
            charge = 2
        reason = (
            f"selected from {sample} observed shots: failure_rate={failure_rate:.1%}, "
            f"average_cost=${avg_cost}, average_latency={avg_seconds:.3f}s. "
            "Charge level is raised only when observed failures justify escalation."
        )
        return DoctrineRecommendation(
            provider=winner.provider,
            model=winner.model,
            suggested_charge_level=charge,
            confidence=confidence,
            reason=reason,
            average_cost=avg_cost,
            average_seconds=avg_seconds,
            failure_rate=failure_rate,
        )
