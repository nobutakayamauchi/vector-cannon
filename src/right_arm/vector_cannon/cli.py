from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .gateway import GatewayError, PROVIDER_SPECS, make_gateway
from .runner import VectorCannon


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
        description="Budget-aware, read-only repository model benchmark runner.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    providers = subparsers.add_parser("providers", help="List approved inference lanes and required key env vars.")
    providers.set_defaults(handler=_providers)

    credits = subparsers.add_parser("credits", help="Show exact provider credit balance when supported.")
    credits.add_argument("--provider", choices=sorted(PROVIDER_SPECS), default="vercel")
    credits.set_defaults(handler=_credits)

    models = subparsers.add_parser("models", help="List models visible through one provider lane.")
    models.add_argument("--provider", choices=sorted(PROVIDER_SPECS), default="vercel")
    models.add_argument("--contains", default="", help="Case-insensitive filter for model id/name.")
    models.set_defaults(handler=_models)

    fire = subparsers.add_parser("fire", help="Fire one bounded read-only repository shot.")
    fire.add_argument("--provider", choices=sorted(PROVIDER_SPECS), default="vercel")
    fire.add_argument("--model", required=True, help="Provider model id.")
    fire.add_argument("--repo", required=True, type=Path, help="Local repository root to expose read-only.")
    fire.add_argument("--prompt", required=True, type=Path, help="Prompt text file.")
    fire.add_argument("--reasoning", choices=["none", "minimal", "low", "medium", "high", "xhigh", "extra_high", "max"], default=None)
    fire.add_argument("--max-usd", type=_positive_decimal, default=Decimal("1.00"), help="Best-effort per-run cost ceiling.")
    fire.add_argument("--max-steps", type=int, default=16, help="Maximum model turns/tool rounds.")
    fire.add_argument("--max-output-tokens", type=int, default=6000, help="Maximum output tokens per model turn.")
    fire.add_argument(
        "--input-price-per-million",
        type=_nonnegative_decimal,
        default=None,
        help="Manual USD per 1M input tokens when the provider catalog lacks trusted pricing.",
    )
    fire.add_argument(
        "--output-price-per-million",
        type=_nonnegative_decimal,
        default=None,
        help="Manual USD per 1M output tokens when the provider catalog lacks trusted pricing.",
    )
    fire.add_argument("--tag", default="shot", help="Run label included in provider metadata and the local log.")
    fire.add_argument("--log-dir", type=Path, default=None, help="Optional log directory; defaults outside the target repo.")
    fire.add_argument("--json", action="store_true", help="Print the complete run result as JSON.")
    fire.set_defaults(handler=_fire)
    return parser


def _providers(args: argparse.Namespace) -> int:
    for provider_id, spec in sorted(PROVIDER_SPECS.items()):
        meter = "exact-credit" if spec.has_exact_credit_meter else "token-metered"
        pricing = "live-catalog" if spec.catalog_pricing_per_token else "manual-price-if-needed"
        print(f"{provider_id}\tkey={spec.key_env}\t{meter}\t{pricing}\t{spec.base_url}")
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
        rows.append(
            (
                model_id,
                name,
                str(input_price) if input_price is not None else "?",
                str(output_price) if output_price is not None else "?",
                model.get("context_window", model.get("context_length", "?")),
            )
        )
    if not rows:
        print("No matching models found.")
        return 1
    for model_id, name, input_price, output_price, context in rows:
        print(f"{model_id}\t{name}\tin={input_price}/tok\tout={output_price}/tok\tctx={context}")
    return 0


def _fire(args: argparse.Namespace) -> int:
    if args.max_steps < 1:
        raise ValueError("--max-steps must be >= 1")
    if args.max_output_tokens < 1:
        raise ValueError("--max-output-tokens must be >= 1")

    result = VectorCannon().fire(
        provider=args.provider,
        model=args.model,
        repository=args.repo,
        prompt_file=args.prompt,
        reasoning_effort=args.reasoning,
        max_usd=args.max_usd,
        max_steps=args.max_steps,
        max_output_tokens=args.max_output_tokens,
        input_price_per_million=args.input_price_per_million,
        output_price_per_million=args.output_price_per_million,
        tag=args.tag,
        log_dir=args.log_dir,
    )
    if args.json:
        from dataclasses import asdict

        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        credits = (
            f" credits=${result.credit_before}->${result.credit_after}"
            if result.credit_before is not None
            else ""
        )
        print(
            f"SHOT {result.tag}: provider={result.provider} model={result.model} "
            f"cost=${result.observed_cost} accounting={result.accounting_mode}{credits} "
            f"tokens={result.input_tokens}/{result.output_tokens} turns={result.model_turns} "
            f"tools={result.tool_calls} files={len(result.files_read)} stop={result.stop_reason}"
        )
        print("\n--- MODEL ANSWER ---\n")
        print(result.answer)
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
