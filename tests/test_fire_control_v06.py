from __future__ import annotations

from decimal import Decimal

from vector_cannon.doctrine import DoctrineAdvisor
from vector_cannon.fireplan import Charge
from vector_cannon.ledger import LedgerRow
from vector_cannon.venue import EnergyLineRouter


class _Gateway:
    def __init__(self, provider: str):
        self.provider_id = provider
        self.has_exact_credit_meter = False

    def list_models(self):
        if self.provider_id == "openrouter":
            return [{"id": "demo/astra", "name": "Astra", "pricing": {"input": "0.000001", "output": "0.000002"}}]
        if self.provider_id == "vercel":
            return [{"id": "demo/astra", "name": "Astra", "pricing": {"input": "0.000002", "output": "0.000003"}}]
        return []

    def declared_free_model(self, model_id: str) -> bool:
        return False

    def catalog_pricing(self, model):
        pricing = model.get("pricing") or {}
        return Decimal(pricing["input"]), Decimal(pricing["output"])


def test_router_selects_cheapest_ready_route(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "x")
    router = EnergyLineRouter(gateway_factory=_Gateway)
    decision = router.choose(
        "astra",
        goal="cheapest",
        max_usd=Decimal("1"),
        estimated_input_tokens=100,
        max_output_tokens=100,
    )
    assert decision.winner.provider == "openrouter"
    assert decision.winner.model_id == "demo/astra"


def test_router_rejects_unarmed_only_route(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    router = EnergyLineRouter(gateway_factory=_Gateway)
    try:
        router.choose("astra", max_usd=Decimal("1"))
    except Exception as exc:
        assert "No READY approved route" in str(exc)
    else:
        raise AssertionError("expected unarmed route rejection")


def test_charge_presets_are_bounded() -> None:
    assert Charge.preset(1).max_output_tokens < Charge.preset(5).max_output_tokens
    assert Charge.preset(5).critique_rounds == 3
    try:
        Charge.preset(6)
    except ValueError:
        pass
    else:
        raise AssertionError("expected invalid charge rejection")


def test_doctrine_uses_observed_failure_cost_latency() -> None:
    rows = [
        LedgerRow("venue-a", "model/a", 10, Decimal("1.00"), 20.0, 1000, 100, 0),
        LedgerRow("venue-b", "model/b", 10, Decimal("0.50"), 10.0, 1000, 100, 5),
    ]
    recommendation = DoctrineAdvisor().recommend(rows, objective="balanced")
    assert recommendation.provider == "venue-a"
    assert recommendation.confidence == "high"
    assert recommendation.suggested_charge_level == 2


def test_doctrine_escalates_charge_when_failures_are_high() -> None:
    rows = [LedgerRow("venue-a", "model/a", 3, Decimal("0.30"), 3.0, 100, 50, 2)]
    recommendation = DoctrineAdvisor().recommend(rows)
    assert recommendation.suggested_charge_level == 4
