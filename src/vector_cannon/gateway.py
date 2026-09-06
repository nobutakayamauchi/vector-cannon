from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


class GatewayError(RuntimeError):
    """Raised when a Vector Cannon provider returns an error."""


@dataclass(frozen=True)
class CreditSnapshot:
    balance: Decimal
    total_used: Decimal

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "CreditSnapshot":
        return cls(
            balance=Decimal(str(payload.get("balance", "0"))),
            total_used=Decimal(str(payload.get("total_used", "0"))),
        )


@dataclass(frozen=True)
class ProviderSpec:
    provider_id: str
    base_url: str
    key_env: str
    model_catalog_auth: bool = True
    credits_path: str | None = None
    catalog_pricing_per_token: bool = False
    reasoning_style: str = "reasoning_object"
    free_model_suffix: str | None = None

    @property
    def has_exact_credit_meter(self) -> bool:
        return self.credits_path is not None

    def is_declared_free_model(self, model_id: str) -> bool:
        return bool(self.free_model_suffix and model_id.endswith(self.free_model_suffix))


PROVIDER_SPECS: dict[str, ProviderSpec] = {
    "vercel": ProviderSpec("vercel", "https://ai-gateway.vercel.sh/v1", "AI_GATEWAY_API_KEY", model_catalog_auth=False, credits_path="/credits", catalog_pricing_per_token=True, reasoning_style="reasoning_object"),
    "openai": ProviderSpec("openai", "https://api.openai.com/v1", "OPENAI_API_KEY", reasoning_style="reasoning_effort"),
    "openrouter": ProviderSpec("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", catalog_pricing_per_token=True, reasoning_style="reasoning_object"),
    "unorouter": ProviderSpec("unorouter", "https://api.unorouter.com/v1", "UNOROUTER_API_KEY", reasoning_style="reasoning_object", free_model_suffix=":free"),
    "gemini": ProviderSpec("gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY", reasoning_style="reasoning_effort"),
    "together": ProviderSpec("together", "https://api.together.ai/v1", "TOGETHER_API_KEY", reasoning_style="reasoning_object"),
    "fireworks": ProviderSpec("fireworks", "https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY", reasoning_style="reasoning_object"),
    "groq": ProviderSpec("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", reasoning_style="reasoning_object"),
}


def get_provider_spec(provider_id: str) -> ProviderSpec:
    try:
        return PROVIDER_SPECS[provider_id]
    except KeyError as exc:
        allowed = ", ".join(sorted(PROVIDER_SPECS))
        raise GatewayError(f"Unknown provider '{provider_id}'. Allowed: {allowed}") from exc


class OpenAICompatibleGateway:
    """Small stdlib-only client for approved OpenAI-compatible inference lanes."""

    def __init__(self, provider_id: str, api_key: str | None = None, *, timeout: int = 180) -> None:
        self.spec = get_provider_spec(provider_id)
        self.api_key = api_key or os.getenv(self.spec.key_env)
        self.base_url = self.spec.base_url.rstrip("/")
        self.timeout = timeout

    @property
    def provider_id(self) -> str:
        return self.spec.provider_id

    @property
    def has_exact_credit_meter(self) -> bool:
        return self.spec.has_exact_credit_meter

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None, *, auth: bool = True) -> dict[str, Any]:
        if auth and not self.api_key:
            raise GatewayError(f"Missing {self.spec.key_env} for provider '{self.provider_id}'.")
        headers = {"Accept": "application/json", "User-Agent": "vector-cannon/0.1"}
        if auth:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.provider_id == "openrouter":
            headers["X-Title"] = "Vector Cannon"
        data: bytes | None = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise GatewayError(f"{self.provider_id} HTTP {exc.code}: {body[:1000]}") from exc
        except urllib.error.URLError as exc:
            raise GatewayError(f"{self.provider_id} connection failed: {exc}") from exc
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GatewayError(f"{self.provider_id} returned non-JSON content.") from exc
        if not isinstance(decoded, dict):
            raise GatewayError(f"{self.provider_id} returned an unexpected response shape.")
        return decoded

    def list_models(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/models", auth=self.spec.model_catalog_auth)
        data = payload.get("data", [])
        return [item for item in data if isinstance(item, dict)]

    def credits(self) -> CreditSnapshot:
        if not self.spec.credits_path:
            raise GatewayError(f"Provider '{self.provider_id}' does not expose an exact credit meter to Vector Cannon.")
        return CreditSnapshot.from_payload(self._request("GET", self.spec.credits_path))

    def chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/chat/completions", payload)

    def find_model(self, model_id: str) -> dict[str, Any] | None:
        try:
            models = self.list_models()
        except GatewayError:
            return None
        return next((model for model in models if model.get("id") == model_id), None)

    def add_reasoning(self, request: dict[str, Any], effort: str | None) -> None:
        if not effort:
            return
        if self.spec.reasoning_style == "reasoning_effort":
            request["reasoning_effort"] = effort
        else:
            request["reasoning"] = {"effort": effort}

    def add_request_metadata(self, request: dict[str, Any], *, tag: str) -> None:
        if self.provider_id == "vercel":
            request["providerOptions"] = {"gateway": {"tags": ["vector-cannon", tag]}}

    def catalog_pricing(self, model: dict[str, Any] | None) -> tuple[Decimal | None, Decimal | None]:
        if not model or not self.spec.catalog_pricing_per_token:
            return None, None
        pricing = model.get("pricing") or {}
        raw_input = pricing.get("input", pricing.get("prompt"))
        raw_output = pricing.get("output", pricing.get("completion"))
        try:
            input_price = Decimal(str(raw_input)) if raw_input is not None else None
            output_price = Decimal(str(raw_output)) if raw_output is not None else None
        except (InvalidOperation, ValueError, TypeError):
            return None, None
        return input_price, output_price

    def declared_free_model(self, model_id: str) -> bool:
        return self.spec.is_declared_free_model(model_id)

    @staticmethod
    def usage_tokens(payload: dict[str, Any]) -> tuple[int, int]:
        usage = payload.get("usage") or {}
        return int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0), int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)

    @staticmethod
    def reported_cost(payload: dict[str, Any]) -> Decimal | None:
        usage = payload.get("usage") or {}
        for field in ("cost", "total_cost"):
            value = usage.get(field)
            if value is None:
                continue
            try:
                return max(Decimal("0"), Decimal(str(value)))
            except (InvalidOperation, ValueError, TypeError):
                continue
        return None


class VercelGateway(OpenAICompatibleGateway):
    def __init__(self, api_key: str | None = None, base_url: str | None = None, timeout: int = 180) -> None:
        super().__init__("vercel", api_key=api_key, timeout=timeout)
        if base_url:
            self.base_url = base_url.rstrip("/")


def make_gateway(provider_id: str) -> OpenAICompatibleGateway:
    return OpenAICompatibleGateway(provider_id)
