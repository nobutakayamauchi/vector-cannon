from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .gateway import PROVIDER_SPECS


SAFE_MODEL = re.compile(r"^[A-Za-z0-9._:/-]{1,240}$")
SAFE_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SAFE_REF = re.compile(r"^[A-Za-z0-9._/-]+$")
SAFE_REASONING = re.compile(r"^[A-Za-z0-9_-]+$")
SAFE_TAG = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
PROMPTS = {
    "repo_recon": "prompts/vector_cannon/repo_recon.md",
}


@dataclass(frozen=True)
class IssueShot:
    provider: str
    model: str
    target_repo: str
    target_ref: str
    prompt: str
    reasoning: str | None
    max_usd: Decimal
    max_steps: int
    max_output_tokens: int
    input_price_per_million: Decimal | None
    output_price_per_million: Decimal | None
    tag: str


def _optional_nonnegative_decimal(values: dict[str, str], key: str) -> Decimal | None:
    raw = values.get(key)
    if raw in (None, ""):
        return None
    try:
        parsed = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"invalid {key}") from exc
    if parsed < 0:
        raise ValueError(f"{key} must be nonnegative")
    return parsed


def parse_issue_body(body: str, *, allowed_owner: str) -> IssueShot:
    values: dict[str, str] = {}
    allowed_fields = {
        "provider",
        "model",
        "target_repo",
        "target_ref",
        "prompt",
        "reasoning",
        "max_usd",
        "max_steps",
        "max_output_tokens",
        "input_price_per_million",
        "output_price_per_million",
        "tag",
    }
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        if key in allowed_fields:
            values[key] = value.strip()

    required = {"model", "target_repo"}
    missing = sorted(required - values.keys())
    if missing:
        raise ValueError("missing fields: " + ", ".join(missing))

    provider = values.get("provider", "vercel").casefold()
    model = values["model"]
    target_repo = values["target_repo"]
    target_ref = values.get("target_ref", "main")
    prompt = values.get("prompt", "repo_recon")
    reasoning = values.get("reasoning") or None
    tag = values.get("tag", "issue-shot")

    if provider not in PROVIDER_SPECS:
        raise ValueError("unsupported provider")
    if not SAFE_MODEL.fullmatch(model) or ".." in model:
        raise ValueError("invalid model id")
    if not SAFE_REPO.fullmatch(target_repo):
        raise ValueError("invalid target_repo")
    if target_repo.split("/", 1)[0].casefold() != allowed_owner.casefold():
        raise ValueError("target_repo owner is not allowed")
    if not SAFE_REF.fullmatch(target_ref) or ".." in target_ref:
        raise ValueError("invalid target_ref")
    if prompt not in PROMPTS:
        raise ValueError("unsupported prompt")
    if reasoning is not None and not SAFE_REASONING.fullmatch(reasoning):
        raise ValueError("invalid reasoning value")
    if not SAFE_TAG.fullmatch(tag):
        raise ValueError("invalid tag")

    try:
        max_usd = Decimal(values.get("max_usd", "1.00"))
        max_steps = int(values.get("max_steps", "16"))
        max_output_tokens = int(values.get("max_output_tokens", "6000"))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid numeric field") from exc
    if not (Decimal("0") < max_usd <= Decimal("50")):
        raise ValueError("max_usd must be >0 and <=50")
    if not (1 <= max_steps <= 40):
        raise ValueError("max_steps must be 1..40")
    if not (1 <= max_output_tokens <= 50000):
        raise ValueError("max_output_tokens must be 1..50000")

    input_price = _optional_nonnegative_decimal(values, "input_price_per_million")
    output_price = _optional_nonnegative_decimal(values, "output_price_per_million")
    if (input_price is None) != (output_price is None):
        raise ValueError("manual pricing requires both input_price_per_million and output_price_per_million")

    return IssueShot(
        provider=provider,
        model=model,
        target_repo=target_repo,
        target_ref=target_ref,
        prompt=PROMPTS[prompt],
        reasoning=reasoning,
        max_usd=max_usd,
        max_steps=max_steps,
        max_output_tokens=max_output_tokens,
        input_price_per_million=input_price,
        output_price_per_million=output_price,
        tag=tag,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("body_file", type=Path)
    parser.add_argument("--allowed-owner", required=True)
    args = parser.parse_args(argv)
    shot = parse_issue_body(args.body_file.read_text(encoding="utf-8"), allowed_owner=args.allowed_owner)
    fields = {
        "provider": shot.provider,
        "model": shot.model,
        "target_repo": shot.target_repo,
        "target_ref": shot.target_ref,
        "prompt": shot.prompt,
        "reasoning": shot.reasoning or "",
        "max_usd": str(shot.max_usd),
        "max_steps": str(shot.max_steps),
        "max_output_tokens": str(shot.max_output_tokens),
        "input_price_per_million": "" if shot.input_price_per_million is None else str(shot.input_price_per_million),
        "output_price_per_million": "" if shot.output_price_per_million is None else str(shot.output_price_per_million),
        "tag": shot.tag,
    }
    for key, value in fields.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
