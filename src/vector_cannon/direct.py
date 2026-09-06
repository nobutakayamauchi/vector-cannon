from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .gateway import CreditSnapshot, GatewayError, OpenAICompatibleGateway, make_gateway

DIRECT_SYSTEM_PROMPT = """You are operating inside Vector Cannon direct inference mode.
Answer the operator's request directly. Do not claim access to tools, files, websites, accounts, or external state unless that context is explicitly included in the prompt.
Be precise about uncertainty. The firing controller enforces budget and approved-provider boundaries outside the model.
"""


@dataclass(frozen=True)
class DirectShotResult:
    kind: str
    tag: str
    provider: str
    model: str
    reasoning_effort: str | None
    started_at: str
    elapsed_seconds: float
    credit_before: str | None
    credit_after: str | None
    observed_cost: str
    accounting_mode: str
    pricing_source: str
    input_tokens: int
    output_tokens: int
    answer: str
    stop_reason: str
    prompt_chars: int


class DirectShot:
    def __init__(self, gateway: OpenAICompatibleGateway | None = None) -> None:
        self.gateway = gateway

    @staticmethod
    def _estimated_input_tokens(messages: list[dict[str, Any]]) -> int:
        chars = len(json.dumps(messages, ensure_ascii=False))
        return max(1, (chars + 2) // 3)

    @staticmethod
    def _credit_delta(before: CreditSnapshot, after: CreditSnapshot) -> Decimal:
        return max(Decimal("0"), before.balance - after.balance)

    @staticmethod
    def _manual_per_token(price_per_million: Decimal | None) -> Decimal | None:
        if price_per_million is None:
            return None
        if price_per_million < 0:
            raise ValueError("manual token price cannot be negative")
        return price_per_million / Decimal("1000000")

    def fire(
        self,
        *,
        prompt: str,
        model: str,
        provider: str = "vercel",
        system_prompt: str | None = None,
        reasoning_effort: str | None = None,
        max_usd: Decimal = Decimal("1.00"),
        max_output_tokens: int = 6000,
        input_price_per_million: Decimal | None = None,
        output_price_per_million: Decimal | None = None,
        tag: str = "direct-shot",
        log_dir: Path | None = None,
    ) -> DirectShotResult:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        if max_usd <= 0:
            raise ValueError("max_usd must be positive")
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be >= 1")

        gateway = self.gateway or make_gateway(provider)
        if self.gateway is not None and provider != "vercel" and provider != self.gateway.provider_id:
            raise ValueError("provider does not match the injected gateway")
        provider = gateway.provider_id

        manual_input = self._manual_per_token(input_price_per_million)
        manual_output = self._manual_per_token(output_price_per_million)
        if (manual_input is None) != (manual_output is None):
            raise ValueError("manual pricing requires both input and output prices")

        catalog_model = gateway.find_model(model)
        if manual_input is not None and manual_output is not None:
            input_price, output_price, pricing_source = manual_input, manual_output, "manual_per_million"
        elif gateway.declared_free_model(model):
            input_price, output_price, pricing_source = Decimal("0"), Decimal("0"), "provider_declared_free"
        else:
            input_price, output_price = gateway.catalog_pricing(catalog_model)
            pricing_source = "provider_catalog"
        if input_price is None or output_price is None:
            raise GatewayError(
                f"Provider '{provider}' does not expose trusted live pricing for model '{model}'. "
                "Pass both --input-price-per-million and --output-price-per-million so the shot can fail closed on budget."
            )

        before: CreditSnapshot | None = None
        if gateway.has_exact_credit_meter:
            before = gateway.credits()
            if before.balance <= 0:
                raise GatewayError(f"Provider '{provider}' credit balance is empty.")

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt or DIRECT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        estimated_input = self._estimated_input_tokens(messages)
        estimated_input_cost = Decimal(estimated_input) * input_price
        if estimated_input_cost >= max_usd:
            raise GatewayError("Direct shot blocked before firing: estimated input cost reaches the shot budget.")

        per_call_max_tokens = max_output_tokens
        if output_price > 0:
            affordable_output = int((max_usd - estimated_input_cost) / output_price)
            if affordable_output <= 0:
                raise GatewayError("Direct shot blocked before firing: no output tokens fit inside the shot budget.")
            per_call_max_tokens = max(1, min(max_output_tokens, affordable_output))

        request: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "max_tokens": per_call_max_tokens,
        }
        gateway.add_request_metadata(request, tag=tag)
        gateway.add_reasoning(request, reasoning_effort)

        started_at = datetime.now(timezone.utc).isoformat()
        start = time.monotonic()
        response = gateway.chat_completion(request)
        elapsed = time.monotonic() - start

        input_tokens, output_tokens = gateway.usage_tokens(response)
        choices = response.get("choices") or []
        if not choices:
            raise GatewayError(f"Provider '{provider}' returned no choices.")
        choice = choices[0]
        message = choice.get("message") or {}
        content = message.get("content")
        answer = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False) if content is not None else ""
        finish_reason = str(choice.get("finish_reason") or "completed")
        stop_reason = "truncated" if finish_reason == "length" else finish_reason

        if gateway.has_exact_credit_meter:
            after = gateway.credits()
            assert before is not None
            observed = self._credit_delta(before, after)
            credit_before, credit_after, accounting_mode = str(before.balance), str(after.balance), "credit_delta"
        else:
            reported = gateway.reported_cost(response)
            if reported is not None:
                observed = reported
                accounting_mode = "provider_reported"
            else:
                observed = Decimal(input_tokens) * input_price + Decimal(output_tokens) * output_price
                accounting_mode = "token_estimate"
            credit_before = credit_after = None

        result = DirectShotResult(
            kind="direct-shot",
            tag=tag,
            provider=provider,
            model=model,
            reasoning_effort=reasoning_effort,
            started_at=started_at,
            elapsed_seconds=round(elapsed, 3),
            credit_before=credit_before,
            credit_after=credit_after,
            observed_cost=str(observed),
            accounting_mode=accounting_mode,
            pricing_source=pricing_source,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            answer=answer,
            stop_reason=stop_reason,
            prompt_chars=len(prompt),
        )
        self._write_log(result, log_dir)
        return result

    @staticmethod
    def _write_log(result: DirectShotResult, log_dir: Path | None) -> Path:
        directory = (log_dir or Path.home() / ".vector-cannon" / "runs").expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        safe_tag = "".join(char if char.isalnum() or char in "-_" else "_" for char in result.tag)
        path = directory / f"{timestamp}-{safe_tag}-{uuid.uuid4().hex[:8]}.json"
        with path.open("x", encoding="utf-8") as handle:
            json.dump(asdict(result), handle, ensure_ascii=False, indent=2)
        return path
