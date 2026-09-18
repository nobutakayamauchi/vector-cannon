from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class LedgerRow:
    provider: str
    model: str
    shots: int
    total_cost: Decimal
    total_seconds: float
    input_tokens: int
    output_tokens: int
    failures: int

    @property
    def average_seconds(self) -> float:
        return self.total_seconds / self.shots if self.shots else 0.0


def default_run_dir() -> Path:
    return Path.home() / ".vector-cannon" / "runs"


def load_runs(run_dir: Path | None = None, *, limit: int | None = None) -> list[dict[str, Any]]:
    directory = (run_dir or default_run_dir()).expanduser()
    if not directory.exists():
        return []
    paths = sorted((path for path in directory.glob("*.json") if path.is_file()), reverse=True)
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        paths = paths[:limit]

    runs: list[dict[str, Any]] = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        payload.setdefault("_log_path", str(path))
        runs.append(payload)
    return runs


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def summarize_runs(runs: Iterable[dict[str, Any]]) -> list[LedgerRow]:
    aggregates: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "shots": 0,
            "cost": Decimal("0"),
            "seconds": 0.0,
            "input": 0,
            "output": 0,
            "failures": 0,
        }
    )

    for run in runs:
        provider = str(run.get("provider") or "unknown")
        model = str(run.get("model") or "unknown")
        bucket = aggregates[(provider, model)]
        bucket["shots"] += 1
        bucket["cost"] += _decimal(run.get("observed_cost"))
        try:
            bucket["seconds"] += float(run.get("elapsed_seconds") or 0.0)
        except (TypeError, ValueError):
            pass
        try:
            bucket["input"] += int(run.get("input_tokens") or 0)
            bucket["output"] += int(run.get("output_tokens") or 0)
        except (TypeError, ValueError):
            pass
        stop_reason = str(run.get("stop_reason") or "completed")
        if stop_reason not in {"completed", "stop"}:
            bucket["failures"] += 1

    rows = [
        LedgerRow(
            provider=provider,
            model=model,
            shots=data["shots"],
            total_cost=data["cost"],
            total_seconds=data["seconds"],
            input_tokens=data["input"],
            output_tokens=data["output"],
            failures=data["failures"],
        )
        for (provider, model), data in aggregates.items()
    ]
    rows.sort(key=lambda row: (-row.shots, row.total_cost, row.provider, row.model))
    return rows
