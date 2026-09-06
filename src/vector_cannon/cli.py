from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .direct import DirectShot
from .doctrine import DoctrineAdvisor
from .fireplan import FireControl
from .gateway import GatewayError, PROVIDER_SPECS, make_gateway
from .ledger import load_runs, summarize_runs
from .runner import VectorCannon
from .venue import EnergyLineRouter, VenueRegistry, latency_index


def _positive_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"invalid decimal value: {value}") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _nonnegative_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"invalid decimal value: {value}") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be nonnegative")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vector-cannon",
        description="Budget-aware multi-provider inference firing controller.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    providers = subparsers.add_parser("providers", help="List approved inference lanes and required key env vars.")
    providers.set_defaults(handler=_providers)

    doctor = subparsers.add_parser("doctor", help="Show which approved inference lanes are locally armed.")
    doctor.add_argument("--json", action="store_true", help="Print readiness as JSON.")
    doctor.set_defaults(handler=_doctor)

    venues = subparsers.add_parser("venues", help="Scan approved firing venues and local readiness.")
    venues.add_argument("--json", action="store_true")
    venues.set_defaults(handler=_venues)

    credits = subparsers.add_parser("credits", help="Show exact provider credit balance when supported.")
    credits.add_argument("--provider", choices=sorted(PROVIDER_SPECS), default="vercel")
    credits.set_defaults(handler=_credits)

    models = subparsers.add_parser("models", help="List models visible through one provider lane.")
    models.add_argument("--provider", choices=sorted(PROVIDER_SPECS), default="vercel")
    models.add_argument("--contains", default="", help="Case-insensitive filter for model id/name.")
    models.set_defaults(handler=_models)

    route = subparsers.add_parser("route", help="Dry-run model discovery and energy-line routing across approved venues.")
    route.add_argument("--model", required=True, help="Model id/name substring such as astra, kimi, claude.")
    route.add_argument("--goal", choices=["cheapest", "fastest", "balanced"], default="balanced")
    route.add_argument("--max-usd", type=_positive_decimal, default=Decimal("1.00"))
    route.add_argument("--estimated-input-tokens", type=int, default=1000)
    route.add_argument("--max-output-tokens", type=int, default=2000)
    route.add_argument("--log-dir", type=Path, default=None)
    route.add_argument("--json", action="store_true")
    route.set_defaults(handler=_route)

    shot = subparsers.add_parser("shot", help="Fire one bounded direct inference shot without exposing a repository.")
    shot.add_argument("--provider", choices=sorted(PROVIDER_SPECS), default="vercel")
    shot.add_argument("--model", required=True, help="Provider model id.")
    prompt_group = shot.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--text", help="Prompt text to fire directly.")
    prompt_group.add_argument("--prompt-file", type=Path, help="UTF-8 prompt file to fire directly.")
    shot.add_argument("--system-file", type=Path, default=None, help="Optional UTF-8 system prompt override.")
    shot.add_argument("--reasoning", choices=["none", "minimal", "low", "medium", "high", "xhigh", "extra_high", "max"], default=None)
    shot.add_argument("--max-usd", type=_positive_decimal, default=Decimal("1.00"), help="Best-effort per-shot cost ceiling.")
    shot.add_argument("--max-output-tokens", type=int, default=6000, help="Maximum output tokens for the shot.")
    shot.add_argument("--input-price-per-million", type=_nonnegative_decimal, default=None, help="Manual USD per 1M input tokens when provider pricing is unavailable.")
    shot.add_argument("--output-price-per-million", type=_nonnegative_decimal, default=None, help="Manual USD per 1M output tokens when provider pricing is unavailable.")
    shot.add_argument("--tag", default="direct-shot", help="Run label included in provider metadata and local ledger.")
    shot.add_argument("--log-dir", type=Path, default=None, help="Optional ledger directory; defaults to ~/.vector-cannon/runs.")
    shot.add_argument("--json", action="store_true", help="Print the complete shot result as JSON.")
    shot.set_defaults(handler=_shot)

    auto = subparsers.add_parser("shot-auto", help="Resolve a model, select an approved route, load charge, and fire.")
    auto.add_argument("--model", required=True, help="Model id/name substring.")
    prompt_group = auto.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--text")
    prompt_group.add_argument("--prompt-file", type=Path)
    auto.add_argument("--goal", choices=["cheapest", "fastest", "balanced"], default="balanced")
    auto.add_argument("--charge", type=int, choices=range(1, 6), default=2)
    auto.add_argument("--role", default="general")
    auto.add_argument("--max-usd", type=_positive_decimal, default=Decimal("1.00"))
    auto.add_argument("--tag", default="auto-shot")
    auto.add_argument("--log-dir", type=Path, default=None)
    auto.add_argument("--dry-run", action="store_true")
    auto.add_argument("--json", action="store_true")
    auto.set_defaults(handler=_shot_auto)

    fire = subparsers.add_parser("fire", help="Fire one bounded read-only repository shot.")
    fire.add_argument("--provider", choices=sorted(PROVIDER_SPECS), default="vercel")
    fire.add_argument("--model", required=True, help="Provider model id.")
    fire.add_argument("--repo", required=True, type=Path, help="Local repository root to expose read-only.")
    fire.add_argument("--prompt", required=True, type=Path, help="Prompt text file.")
    fire.add_argument("--reasoning", choices=["none", "minimal", "low", "medium", "high", "xhigh", "extra_high", "max"], default=None)
    fire.add_argument("--max-usd", type=_positive_decimal, default=Decimal("1.00"), help="Best-effort per-run cost ceiling.")
    fire.add_argument("--max-steps", type=int, default=16, help="Maximum model turns/tool rounds.")
    fire.add_argument("--max-output-tokens", type=int, default=6000, help="Maximum output tokens per model turn.")
    fire.add_argument("--input-price-per-million", type=_nonnegative_decimal, default=None, help="Manual USD per 1M input tokens when provider pricing is unavailable.")
    fire.add_argument("--output-price-per-million", type=_nonnegative_decimal, default=None, help="Manual USD per 1M output tokens when provider pricing is unavailable.")
    fire.add_argument("--tag", default="shot", help="Run label included in provider metadata and local log.")
    fire.add_argument("--log-dir", type=Path, default=None, help="Optional log directory; defaults outside the target repo.")
    fire.add_argument("--json", action="store_true", help="Print the complete run result as JSON.")
    fire.set_defaults(handler=_fire)

    ledger = subparsers.add_parser("ledger", help="Summarize personal firing history by provider and model.")
    ledger.add_argument("--log-dir", type=Path, default=None, help="Ledger directory; defaults to ~/.vector-cannon/runs.")
    ledger.add_argument("--limit", type=int, default=None, help="Only inspect the newest N run files.")
    ledger.add_argument("--json", action="store_true", help="Print summary as JSON.")
    ledger.set_defaults(handler=_ledger)

    doctrine = subparsers.add_parser("doctrine", help="Recommend a firing doctrine from observed ledger evidence.")
    doctrine.add_argument("--objective", choices=["cheapest", "fastest", "reliable", "balanced"], default="balanced")
    doctrine.add_argument("--model-contains", default="")
    doctrine.add_argument("--log-dir", type=Path, default=None)
    doctrine.add_argument("--limit", type=int, default=None)
    doctrine.add_argument("--json", action="store_true")
    doctrine.set_defaults(handler=_doctrine)
    return parser


def _providers(args: argparse.Namespace) -> int:
    for provider_id, spec in sorted(PROVIDER_SPECS.items()):
        meter = "exact-credit" if spec.has_exact_credit_meter else "token-metered"
        pricing = "live-catalog" if spec.catalog_pricing_per_token else "manual-price-if-needed"
        print(f"{provider_id}\tkey={spec.key_env}\t{meter}\t{pricing}\t{spec.base_url}")
    return 0


def _doctor(args: argparse.Namespace) -> int:
    rows = []
    for provider_id, spec in sorted(PROVIDER_SPECS.items()):
        configured = bool(os.getenv(spec.key_env))
        rows.append({"provider": provider_id, "status": "READY" if configured else "UNARMED", "key_env": spec.key_env, "base_url": spec.base_url, "exact_credit_meter": spec.has_exact_credit_meter, "live_catalog_pricing": spec.catalog_pricing_per_token})
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for row in rows:
            print(f"{row['status']}\t{row['provider']}\tkey={row['key_env']}\tcredit={'exact' if row['exact_credit_meter'] else 'token'}\tpricing={'live' if row['live_catalog_pricing'] else 'manual-if-needed'}")
    return 0


def _venues(args: argparse.Namespace) -> int:
    rows = VenueRegistry().statuses()
    if args.json:
        print(json.dumps([asdict(row) for row in rows], ensure_ascii=False, indent=2))
    else:
        print("RESOURCE SCAN")
        for row in rows:
            print(f"{'READY' if row.ready else 'UNARMED'}\t{row.provider}\tkey={row.key_env}\t{row.base_url}")
        print("ENERGY LINE — AVAILABLE LANES IDENTIFIED")
    return 0


def _credits(args: argparse.Namespace) -> int:
    gateway = make_gateway(args.provider)
    snapshot = gateway.credits()
    print(f"{args.provider} balance: ${snapshot.balance}")
    print(f"{args.provider} lifetime used: ${snapshot.total_used}")
    return 0


def _models(args: argparse.Namespace) -> int:
    needle = args.contains.casefold()
    gateway = make_gateway(args.provider)
    rows = []
    for model in gateway.list_models():
        model_id = str(model.get("id", ""))
        name = str(model.get("name", ""))
        if needle and needle not in model_id.casefold() and needle not in name.casefold():
            continue
        input_price, output_price = gateway.catalog_pricing(model)
        rows.append((model_id, name, str(input_price) if input_price is not None else "?", str(output_price) if output_price is not None else "?", model.get("context_window", model.get("context_length", "?"))))
    if not rows:
        print("No matching models found.")
        return 1
    for model_id, name, input_price, output_price, context in rows:
        print(f"{model_id}\t{name}\tin={input_price}/tok\tout={output_price}/tok\tctx={context}")
    return 0


def _router_from_ledger(log_dir: Path | None) -> EnergyLineRouter:
    return EnergyLineRouter(ledger_latency=latency_index(summarize_runs(load_runs(log_dir))))


def _route(args: argparse.Namespace) -> int:
    if args.estimated_input_tokens < 1 or args.max_output_tokens < 1:
        raise ValueError("token estimates must be >= 1")
    decision = _router_from_ledger(args.log_dir).choose(args.model, goal=args.goal, max_usd=args.max_usd, estimated_input_tokens=args.estimated_input_tokens, max_output_tokens=args.max_output_tokens)
    if args.json:
        print(json.dumps(asdict(decision), ensure_ascii=False, indent=2, default=str))
        return 0
    print(f"TARGET: {decision.target}")
    for candidate in decision.candidates:
        cost = "?" if candidate.estimated_cost is None else f"${candidate.estimated_cost}"
        latency = "?" if candidate.ledger_average_seconds is None else f"{candidate.ledger_average_seconds:.3f}s"
        print(f"{'READY' if candidate.ready else 'UNARMED'}\t{candidate.provider}/{candidate.model_id}\tcost={cost}\tlatency={latency}\tprice={candidate.price_confidence}")
    winner = decision.winner
    print(f"\nRECOMMENDED ROUTE: {winner.provider}/{winner.model_id}")
    print(f"REASON: {decision.reason}")
    print("ENERGY LINE — CONNECTED")
    print("READY TO FIRE")
    return 0


def _read_prompt(text: str | None, prompt_file: Path | None) -> str:
    if prompt_file is not None:
        path = prompt_file.expanduser()
        if not path.is_file():
            raise ValueError(f"Prompt file does not exist: {path}")
        return path.read_text(encoding="utf-8")
    return str(text or "")


def _shot(args: argparse.Namespace) -> int:
    if args.max_output_tokens < 1:
        raise ValueError("--max-output-tokens must be >= 1")
    prompt = _read_prompt(args.text, args.prompt_file)
    system_prompt = None
    if args.system_file is not None:
        system_file = args.system_file.expanduser()
        if not system_file.is_file():
            raise ValueError(f"System prompt file does not exist: {system_file}")
        system_prompt = system_file.read_text(encoding="utf-8")
    result = DirectShot().fire(provider=args.provider, model=args.model, prompt=prompt, system_prompt=system_prompt, reasoning_effort=args.reasoning, max_usd=args.max_usd, max_output_tokens=args.max_output_tokens, input_price_per_million=args.input_price_per_million, output_price_per_million=args.output_price_per_million, tag=args.tag, log_dir=args.log_dir)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        credits = f" credits=${result.credit_before}->${result.credit_after}" if result.credit_before is not None else ""
        print(f"FIRE {result.tag}: provider={result.provider} model={result.model} cost=${result.observed_cost} accounting={result.accounting_mode}{credits} tokens={result.input_tokens}/{result.output_tokens} time={result.elapsed_seconds}s stop={result.stop_reason}")
        print("\n--- MODEL ANSWER ---\n")
        print(result.answer)
    return 0


def _shot_auto(args: argparse.Namespace) -> int:
    mission = _read_prompt(args.text, args.prompt_file)
    controller = FireControl(router=_router_from_ledger(args.log_dir))
    plan = controller.plan(mission=mission, target=args.model, goal=args.goal, max_usd=args.max_usd, charge_level=args.charge, role=args.role)
    if args.dry_run:
        print(plan.render())
        return 0
    result = controller.fire(plan, log_dir=args.log_dir, tag=args.tag)
    if args.json:
        print(json.dumps({"plan": asdict(plan), "result": asdict(result)}, ensure_ascii=False, indent=2, default=str))
    else:
        print(plan.render())
        print("FIRE")
        print(f"IMPACT: cost=${result.observed_cost} time={result.elapsed_seconds}s stop={result.stop_reason}")
        print("\n--- MODEL ANSWER ---\n")
        print(result.answer)
    return 0


def _fire(args: argparse.Namespace) -> int:
    if args.max_steps < 1:
        raise ValueError("--max-steps must be >= 1")
    if args.max_output_tokens < 1:
        raise ValueError("--max-output-tokens must be >= 1")
    result = VectorCannon().fire(provider=args.provider, model=args.model, repository=args.repo, prompt_file=args.prompt, reasoning_effort=args.reasoning, max_usd=args.max_usd, max_steps=args.max_steps, max_output_tokens=args.max_output_tokens, input_price_per_million=args.input_price_per_million, output_price_per_million=args.output_price_per_million, tag=args.tag, log_dir=args.log_dir)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        credits = f" credits=${result.credit_before}->${result.credit_after}" if result.credit_before is not None else ""
        print(f"SHOT {result.tag}: provider={result.provider} model={result.model} cost=${result.observed_cost} accounting={result.accounting_mode}{credits} tokens={result.input_tokens}/{result.output_tokens} turns={result.model_turns} tools={result.tool_calls} files={len(result.files_read)} stop={result.stop_reason}")
        print("\n--- MODEL ANSWER ---\n")
        print(result.answer)
    return 0


def _ledger(args: argparse.Namespace) -> int:
    rows = summarize_runs(load_runs(args.log_dir, limit=args.limit))
    if args.json:
        payload = [{**asdict(row), "total_cost": str(row.total_cost), "average_seconds": round(row.average_seconds, 3)} for row in rows]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("No Vector Cannon firing records found.")
        return 0
    print("SHOTS\tCOST\tAVG_S\tFAIL\tTOKENS_IN/OUT\tPROVIDER\tMODEL")
    for row in rows:
        print(f"{row.shots}\t${row.total_cost}\t{row.average_seconds:.3f}\t{row.failures}\t{row.input_tokens}/{row.output_tokens}\t{row.provider}\t{row.model}")
    return 0


def _doctrine(args: argparse.Namespace) -> int:
    rows = summarize_runs(load_runs(args.log_dir, limit=args.limit))
    recommendation = DoctrineAdvisor().recommend(rows, objective=args.objective, model_contains=args.model_contains)
    if args.json:
        print(json.dumps(asdict(recommendation), ensure_ascii=False, indent=2, default=str))
    else:
        print("FIRING DOCTRINE")
        print(f"WARHEAD -> {recommendation.provider}/{recommendation.model}")
        print(f"CHARGE -> {recommendation.suggested_charge_level}")
        print(f"CONFIDENCE -> {recommendation.confidence}")
        print(f"EVIDENCE -> {recommendation.reason}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (GatewayError, ValueError, OSError) as exc:
        print(f"vector-cannon: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
