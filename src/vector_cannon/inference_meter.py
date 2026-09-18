from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

VALID_STATUS = {"MEASURED", "DERIVED", "ESTIMATED", "UNKNOWN"}
VALID_PHASE = {"start", "end", "checkpoint"}
VALID_METRIC = {"quota", "internal_inference", "cost"}
VALID_SOURCE = {"api", "ui", "manual", "fixture"}
# A scale identifies the source's measurement definition, not a screenshot URL.
COMPARISON_FIELDS = {
    "metric_kind": "METRIC", "unit": "UNIT", "source_scale": "SOURCE_SCALE",
    "provider": "PROVIDER", "scope_id": "SCOPE", "plan_id": "PLAN",
    "period_id": "PERIOD", "period_start": "PERIOD", "period_end": "PERIOD",
    "reset_id": "RESET_INTERVAL",
}
METADATA_FIELDS = (
    "metric_kind", "unit", "source_method", "source_ref", "source_scale",
    "provider", "plan_id", "scope_id", "period_id", "period_start", "period_end",
    "reset_id", "concurrent_usage", "reset_detected", "exclusive_evidence",
)


@dataclass(frozen=True)
class InferenceSnapshot:
    # timestamp is the source observation time; never the time of loading a file.
    timestamp: str | None
    launcher: str
    shot_id: str
    phase: str
    total_inference: float | None = None
    remaining_inference: float | None = None
    status: str = "UNKNOWN"
    note: str = ""
    metric_kind: str | None = None
    unit: str | None = None
    limit_value: float | None = None
    remaining_value: float | None = None
    used_value: float | None = None
    source_method: str | None = None
    source_ref: str | None = None
    source_scale: str | None = None
    recorded_at: str | None = None
    provider: str | None = None
    plan_id: str | None = None
    scope_id: str | None = None
    period_id: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    reset_id: str | None = None
    concurrent_usage: bool | None = None
    reset_detected: bool | None = None
    exclusive_evidence: str | None = None
    reasons: tuple[str, ...] = ()
    # Invalid or untyped legacy fields remain inspectable, outside typed values.
    raw_fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ShotInferenceUsage:
    shot_id: str
    launcher: str
    started_at: str | None
    ended_at: str | None
    total_inference: float | None
    start_remaining: float | None
    end_remaining: float | None
    inference_used: float | None
    consumption_percent: float | None
    status: str
    metric_kind: str | None
    unit: str | None
    delta_unit: str | None
    display_delta: float | None
    display_status: str
    attributed_usage: float | None
    reasons: tuple[str, ...]
    display_reasons: tuple[str, ...]
    observations: tuple[InferenceSnapshot, ...]


@dataclass(frozen=True)
class InferenceWindowUsage:
    window_start: str
    window_end: str
    inference_used: float | None
    known_used: float | None
    shot_count: int
    unknown_shots: int
    status: str
    attributed_usage: float | None
    known_attributed_usage: float
    delta_unit: str | None
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class InferenceBudgetSummary:
    launcher: str
    total_inference: float | None
    latest_remaining: float | None
    total_used: float | None
    total_used_percent: float | None
    current_window_used: float | None
    current_window_known_used: float | None
    current_window_start: str | None
    current_window_end: str | None
    shots: tuple[ShotInferenceUsage, ...]
    windows: tuple[InferenceWindowUsage, ...]
    status: str
    metric_kind: str | None
    unit: str | None
    delta_unit: str | None
    limit_value: float | None
    latest_remaining_value: float | None
    total_attributed_usage: float | None
    known_attributed_usage: float | None
    reasons: tuple[str, ...]
    unassigned_shot_ids: tuple[str, ...]
    window_hours: float


def default_snapshot_path() -> Path:
    return Path.home() / ".vector-cannon" / "inference" / "snapshots.jsonl"


def _reasons(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


def _identity(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value and value.upper() not in {"UNKNOWN", "NULL", "NONE"} else None


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("boolean is not an observation value")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("observation value must be numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError("observation value must be finite and non-negative")
    return number


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("observation times require a timezone")
    return parsed.astimezone(timezone.utc)


def _time(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return _parse_time(value)
    except (ValueError, TypeError, AttributeError):
        return None


def _raw(value: Any) -> Any:
    # JSON null means unknown; non-finite input is retained as diagnostic text.
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if isinstance(value, dict):
        return {str(key): _raw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_raw(item) for item in value]
    return value


def _snapshot(payload: dict[str, Any]) -> InferenceSnapshot:
    reasons = [str(value) for value in payload.get("reasons", ())]
    raw = _raw(dict(payload.get("raw_fields") or {}))
    data: dict[str, Any] = {}
    for name in METADATA_FIELDS:
        if name in {"concurrent_usage", "reset_detected"}:
            value = payload.get(name)
            data[name] = value if isinstance(value, bool) else None
            if value is not None and not isinstance(value, bool):
                reasons.append(f"INVALID_{name.upper()}")
                raw[name] = _raw(value)
        else:
            data[name] = _identity(payload.get(name))
    if data["metric_kind"] not in VALID_METRIC:
        reasons.append("METRIC_UNKNOWN")
    if data["source_method"] not in VALID_SOURCE:
        reasons.append("SOURCE_METHOD_UNKNOWN")
    for name in (*COMPARISON_FIELDS, "source_ref"):
        if data[name] is None:
            reasons.append(f"{COMPARISON_FIELDS.get(name, name.upper())}_UNKNOWN")

    for name in ("timestamp", "recorded_at", "period_start", "period_end"):
        value = payload.get(name)
        parsed = _time(value)
        data[name] = parsed.isoformat() if parsed is not None else None
        if value is not None and parsed is None:
            raw[name] = _raw(value)
            reasons.append(f"INVALID_{name.upper()}")
        if name != "recorded_at" and parsed is None:
            reasons.append(f"{name.upper()}_UNKNOWN")

    for name, legacy in (("limit_value", "total_inference"),
                         ("remaining_value", "remaining_inference"),
                         ("used_value", None)):
        value = payload.get(name)
        old = payload.get(legacy) if legacy else None
        if value is None:
            value = old
        elif old is not None and value != old:
            reasons.append(f"CONFLICTING_{name.upper()}")
            raw[legacy] = _raw(old)
        if old is not None and data["metric_kind"] != "internal_inference":
            raw[legacy] = _raw(old)
        try:
            data[name] = _number(value)
        except ValueError:
            data[name] = None
            reasons.append(f"INVALID_{name.upper()}")
            raw[name] = _raw(value)
    if all(data[name] is None for name in ("limit_value", "remaining_value", "used_value")):
        reasons.append("MISSING_VALUE")
    if data["unit"] == "percent":
        if data["metric_kind"] != "quota":
            reasons.append("METRIC_UNIT_MISMATCH")
        for name in ("limit_value", "remaining_value", "used_value"):
            if data[name] is not None and data[name] > 100:
                raw[name] = data[name]
                data[name] = None
                reasons.append(f"INVALID_{name.upper()}")
    limit, remaining = data["limit_value"], data["remaining_value"]
    if limit is not None and remaining is not None and remaining > limit:
        reasons.append("REMAINING_EXCEEDS_LIMIT")
    period_start, period_end = _time(data["period_start"]), _time(data["period_end"])
    observed = _time(data["timestamp"])
    if period_start is not None and period_end is not None:
        if period_end <= period_start:
            reasons.append("INVALID_PERIOD")
        if observed is not None and not period_start <= observed < period_end:
            reasons.append("PERIOD_BOUNDARY")
    status = str(payload.get("status") or "UNKNOWN").upper()
    if status not in VALID_STATUS:
        raw["status"] = status
        status = "UNKNOWN"
        reasons.append("INVALID_STATUS")
    if status == "UNKNOWN":
        reasons.append("UNKNOWN_OBSERVATION")
    phase = str(payload.get("phase") or "unknown").lower()
    if phase not in VALID_PHASE:
        reasons.append("PHASE_UNKNOWN")
    launcher = _identity(payload.get("launcher"))
    shot_id = _identity(payload.get("shot_id"))
    if launcher is None or shot_id is None:
        reasons.append("IDENTITY_UNKNOWN")
    internal = data["metric_kind"] == "internal_inference"
    return InferenceSnapshot(
        **data, launcher=launcher or "unknown", shot_id=shot_id or "unknown", phase=phase,
        total_inference=data["limit_value"] if internal else None,
        remaining_inference=data["remaining_value"] if internal else None,
        status="UNKNOWN" if reasons else status, note=str(payload.get("note") or ""),
        reasons=_reasons(reasons), raw_fields=raw,
    )


def record_snapshot(
    *, launcher: str, shot_id: str, phase: str,
    total_inference: float | None = None, remaining_inference: float | None = None,
    limit_value: float | None = None, remaining_value: float | None = None,
    used_value: float | None = None, status: str = "MEASURED", note: str = "",
    timestamp: str | None = None, recorded_at: str | None = None,
    path: Path | None = None, **metadata: Any,
) -> InferenceSnapshot:
    if not _identity(launcher) or not _identity(shot_id):
        raise ValueError("launcher and shot_id must be known non-empty identifiers")
    if phase.strip().lower() not in VALID_PHASE or status.strip().upper() not in VALID_STATUS:
        raise ValueError("invalid phase or status")
    if set(metadata) - set(METADATA_FIELDS):
        raise TypeError(f"unknown observation fields: {sorted(set(metadata) - set(METADATA_FIELDS))}")
    snapshot = _snapshot(dict(
        metadata, launcher=launcher, shot_id=shot_id, phase=phase.strip().lower(),
        total_inference=total_inference, remaining_inference=remaining_inference,
        limit_value=limit_value, remaining_value=remaining_value, used_value=used_value,
        status=status.strip().upper(), note=note, timestamp=timestamp,
        recorded_at=recorded_at if recorded_at is not None else datetime.now(timezone.utc).isoformat(),
    ))
    target = (path or default_snapshot_path()).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(snapshot), ensure_ascii=False, allow_nan=False) + "\n")
    return snapshot


def load_snapshots(path: Path | None = None) -> list[InferenceSnapshot]:
    target = (path or default_snapshot_path()).expanduser()
    if not target.exists():
        return []
    rows = []
    for line_number, line in enumerate(target.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("snapshot must be an object")
            rows.append(_snapshot(payload))
        except (ValueError, TypeError) as exc:
            # A corrupt line must not disappear and make an incomplete log look complete.
            raise ValueError(f"invalid snapshot record on line {line_number}") from exc
    return sorted(rows, key=lambda row: (row.timestamp is None, row.timestamp or ""))


def _comparison_reasons(rows: list[InferenceSnapshot]) -> list[str]:
    reasons = []
    for name, label in COMPARISON_FIELDS.items():
        values = [getattr(row, name) for row in rows]
        if not values or any(value is None for value in values):
            reasons.append(f"{label}_UNKNOWN")
        if len(set(values)) > 1:
            reasons.append(f"{label}_CHANGED")
    limits = [row.limit_value for row in rows]
    if any(value is None for value in limits):
        reasons.append("LIMIT_UNKNOWN")
    if len(set(limits)) > 1:
        reasons.append("LIMIT_CHANGED")
    return reasons


def _consensus(rows: list[InferenceSnapshot], name: str) -> Any:
    values = {getattr(row, name) for row in rows}
    return next(iter(values)) if len(values) == 1 else None


def _delta_unit(metric: str | None, unit: str | None) -> str | None:
    return "percentage_points" if metric == "quota" and unit == "percent" else unit


def _overlap_reasons(
    key: tuple[str, str], rows: list[InferenceSnapshot],
    groups: dict[tuple[str, str], list[InferenceSnapshot]],
) -> list[str]:
    def bounds(items: list[InferenceSnapshot]) -> tuple[datetime | None, datetime | None]:
        starts = [_time(row.timestamp) for row in items if row.phase == "start"]
        ends = [_time(row.timestamp) for row in items if row.phase == "end"]
        start = starts[0] if len(starts) == 1 else None
        end = ends[0] if len(ends) == 1 else None
        if start is not None and end is not None and end <= start:
            return None, None
        return start, end

    scopes = {(row.provider, row.scope_id) for row in rows if row.provider and row.scope_id}
    start, end = bounds(rows)
    reasons = []
    for other_key, other in groups.items():
        if other_key == key or not scopes.intersection(
            (row.provider, row.scope_id) for row in other if row.provider and row.scope_id
        ):
            continue
        other_start, other_end = bounds(other)
        if end is not None and other_start is not None and end <= other_start:
            continue
        if other_end is not None and start is not None and other_end <= start:
            continue
        complete = all(value is not None for value in (start, end, other_start, other_end))
        reasons.append("CONCURRENT_USAGE" if complete else "OVERLAP_UNKNOWN")
    return reasons


def _shot_usage(
    shot_id: str, rows: list[InferenceSnapshot], *, overlap_reasons: Iterable[str] = (),
) -> ShotInferenceUsage:
    rows = sorted(rows, key=lambda row: (row.timestamp is None, row.timestamp or ""))
    starts = [row for row in rows if row.phase == "start"]
    ends = [row for row in rows if row.phase == "end"]
    start = starts[0] if len(starts) == 1 else None
    end = ends[0] if len(ends) == 1 else None
    problems = _comparison_reasons(rows)
    for phase, items in (("START", starts), ("END", ends)):
        if not items:
            problems.append(f"MISSING_{phase}")
        elif len(items) != 1:
            problems.append(f"AMBIGUOUS_{phase}")
    problems.extend(reason for row in rows for reason in row.reasons)
    if any(row.status == "UNKNOWN" for row in rows):
        problems.append("UNKNOWN_OBSERVATION")
    if any(row.reset_detected is True for row in rows):
        problems.append("RESET_DETECTED")
    if any(row.reset_detected is None for row in rows):
        problems.append("RESET_STATE_UNKNOWN")
    if any(row.remaining_value is None for row in rows):
        problems.append("REMAINING_NOT_OBSERVED")
    if start is not None and end is not None:
        began, finished = _time(start.timestamp), _time(end.timestamp)
        if began is None or finished is None:
            problems.append("TIMESTAMP_UNKNOWN")
        elif finished <= began:
            problems.append("TIME_ORDER_INVALID")
        elif any(_time(row.timestamp) is None or not began <= _parse_time(row.timestamp) <= finished
                 for row in rows):
            problems.append("CHECKPOINT_OUTSIDE_SHOT")
    remaining = [row.remaining_value for row in rows if row.remaining_value is not None]
    if any(after > before for before, after in zip(remaining, remaining[1:])):
        problems.append("REMAINING_INCREASED")
    metric, unit = _consensus(rows, "metric_kind"), _consensus(rows, "unit")
    delta_unit = _delta_unit(metric, unit)
    display_delta = None
    if not problems and start is not None and end is not None:
        display_delta = start.remaining_value - end.remaining_value
    estimated = any(row.status == "ESTIMATED" for row in rows)
    display_status = "UNKNOWN" if display_delta is None else "ESTIMATED" if estimated else "DERIVED"
    attribution = list(problems) + list(overlap_reasons)
    if any(row.concurrent_usage is True for row in rows):
        attribution.append("CONCURRENT_USAGE")
    if any(row.concurrent_usage is None for row in rows):
        attribution.append("CONCURRENCY_UNKNOWN")
    if any(row.exclusive_evidence is None for row in rows):
        attribution.append("EXCLUSIVITY_UNPROVEN")
    if estimated:
        attribution.append("ESTIMATED_OBSERVATION")
    used = display_delta if not attribution else None
    internal = metric == "internal_inference"
    total = _consensus(rows, "limit_value")
    return ShotInferenceUsage(
        shot_id=shot_id, launcher=rows[0].launcher,
        started_at=start.timestamp if start else None, ended_at=end.timestamp if end else None,
        total_inference=total if internal else None,
        start_remaining=start.remaining_value if internal and start else None,
        end_remaining=end.remaining_value if internal and end else None,
        inference_used=used if internal else None,
        consumption_percent=used / total * 100 if internal and used is not None and total else None,
        status="DERIVED" if used is not None else "UNKNOWN", metric_kind=metric, unit=unit,
        delta_unit=delta_unit, display_delta=display_delta, display_status=display_status,
        attributed_usage=used, reasons=_reasons(attribution), display_reasons=_reasons(problems),
        observations=tuple(rows),
    )


def _window_usage(
    shots: tuple[ShotInferenceUsage, ...], *, window_hours: float, anchor: datetime,
) -> tuple[tuple[InferenceWindowUsage, ...], tuple[str, ...]]:
    # Preserve the existing explicit reporting buckets. Provider-period accounting is deferred.
    buckets: dict[int, list[ShotInferenceUsage]] = {}
    unassigned = []
    width_seconds = window_hours * 3600.0
    for shot in shots:
        started, ended = _time(shot.started_at), _time(shot.ended_at)
        if started is None or any(reason in shot.reasons for reason in (
            "PERIOD_CHANGED", "PERIOD_BOUNDARY", "TIME_ORDER_INVALID", "AMBIGUOUS_END",
        )):
            unassigned.append(shot.shot_id)
            continue
        index = math.floor((started - anchor).total_seconds() / width_seconds)
        window_end = anchor + timedelta(hours=window_hours * (index + 1))
        if ended is not None and ended > window_end:
            unassigned.append(shot.shot_id)
            continue
        buckets.setdefault(index, []).append(shot)
    windows = []
    for index, bucket in sorted(buckets.items()):
        known = sum(shot.attributed_usage for shot in bucket if shot.attributed_usage is not None
                    and shot.status == "DERIVED")
        unknown = sum(shot.attributed_usage is None for shot in bucket)
        used = known if unknown == 0 and not unassigned else None
        reasons = [reason for shot in bucket for reason in shot.reasons]
        if unassigned:
            reasons.append("WINDOW_ALLOCATION_UNKNOWN")
        internal = bucket[0].metric_kind == "internal_inference"
        windows.append(InferenceWindowUsage(
            window_start=(anchor + timedelta(hours=window_hours * index)).isoformat(),
            window_end=(anchor + timedelta(hours=window_hours * (index + 1))).isoformat(),
            inference_used=used if internal else None, known_used=known if internal else None,
            shot_count=len(bucket), unknown_shots=unknown, status="DERIVED" if used is not None else "UNKNOWN",
            attributed_usage=used, known_attributed_usage=known, delta_unit=bucket[0].delta_unit,
            reasons=_reasons(reasons),
        ))
    return tuple(windows), tuple(unassigned)


def summarize_inference(
    snapshots: Iterable[InferenceSnapshot], *, launcher: str,
    window_hours: float = 5.0, window_anchor: str | None = None,
) -> InferenceBudgetSummary:
    if not math.isfinite(window_hours) or window_hours <= 0:
        raise ValueError("window_hours must be finite and positive")
    all_rows = [_snapshot(asdict(row)) for row in snapshots]
    groups: dict[tuple[str, str], list[InferenceSnapshot]] = {}
    for row in all_rows:
        groups.setdefault((row.launcher, row.shot_id), []).append(row)
    rows = [row for row in all_rows if row.launcher == launcher]
    selected = [(key, items) for key, items in groups.items() if key[0] == launcher]
    shots = tuple(_shot_usage(key[1], items, overlap_reasons=_overlap_reasons(key, items, groups))
                  for key, items in selected)
    comparison = _comparison_reasons(rows)
    homogeneous = bool(rows) and not comparison
    reasons = comparison + [reason for shot in shots for reason in shot.reasons]
    if not homogeneous:
        reasons.append("GROUPED_ACCOUNTING_REQUIRED" if rows else "NO_OBSERVATIONS")
    metric = _consensus(rows, "metric_kind") if homogeneous else None
    unit = _consensus(rows, "unit") if homogeneous else None
    internal = metric == "internal_inference"
    known = (sum(shot.attributed_usage for shot in shots if shot.attributed_usage is not None
                 and shot.status == "DERIVED") if homogeneous else None)
    total_used = known if homogeneous and all(shot.attributed_usage is not None for shot in shots) else None
    total = _consensus(rows, "limit_value") if homogeneous else None
    # Unknown timestamps/values cannot be replaced by the latest available non-null value.
    latest_remaining = None
    if homogeneous and all(row.timestamp is not None for row in rows):
        latest_time = max(row.timestamp for row in rows)
        latest = [row for row in rows if row.timestamp == latest_time]
        if all(row.status in {"MEASURED", "DERIVED"} for row in latest):
            latest_remaining = _consensus(latest, "remaining_value")
    windows: tuple[InferenceWindowUsage, ...] = ()
    unassigned = tuple(shot.shot_id for shot in shots)
    if homogeneous and window_anchor is not None:
        windows, unassigned = _window_usage(shots, window_hours=window_hours, anchor=_parse_time(window_anchor))
    elif window_anchor is None:
        reasons.append("WINDOW_ANCHOR_UNKNOWN")
    if unassigned:
        reasons.append("WINDOW_ALLOCATION_UNKNOWN")
    current = windows[-1] if windows else None
    if total_used is None:
        reasons.append("TOTAL_USAGE_UNKNOWN")
    return InferenceBudgetSummary(
        launcher=launcher, total_inference=total if internal else None,
        latest_remaining=latest_remaining if internal else None,
        total_used=total_used if internal else None,
        total_used_percent=total_used / total * 100 if internal and total_used is not None and total else None,
        current_window_used=current.inference_used if current else None,
        current_window_known_used=current.known_used if current else None,
        current_window_start=current.window_start if current else None,
        current_window_end=current.window_end if current else None,
        shots=shots, windows=windows, status="UNKNOWN" if reasons else "DERIVED",
        metric_kind=metric, unit=unit, delta_unit=_delta_unit(metric, unit),
        limit_value=total, latest_remaining_value=latest_remaining,
        total_attributed_usage=total_used, known_attributed_usage=known,
        reasons=_reasons(reasons), unassigned_shot_ids=unassigned, window_hours=window_hours,
    )


def _fmt(value: float | None) -> str:
    return "UNKNOWN" if value is None else f"{value:.3f}".rstrip("0").rstrip(".")


def _pct(value: float | None) -> str:
    return "UNKNOWN" if value is None else f"{value:.2f}%"


def render_console(summary: InferenceBudgetSummary) -> str:
    current = summary.windows[-1] if summary.windows else None
    current_used = _fmt(current.attributed_usage if current else None)
    if current and current.attributed_usage is None and current.known_attributed_usage > 0:
        current_used += f" (known >= {_fmt(current.known_attributed_usage)})"
    lines = [
        "VECTOR CANNON / OBSERVATION METER", f"LAUNCHER              {summary.launcher}",
        f"METRIC                {summary.metric_kind or 'UNKNOWN'}",
        f"UNIT                  {summary.unit or 'UNKNOWN'}",
        f"DELTA UNIT            {summary.delta_unit or 'UNKNOWN'}", f"STATUS                {summary.status}",
        f"REASONS               {', '.join(summary.reasons) or 'none'}",
        f"OBSERVED LIMIT        {_fmt(summary.limit_value)}",
        f"OBSERVED REMAINING    {_fmt(summary.latest_remaining_value)}",
        f"TOTAL SHOT USED       {_fmt(summary.total_attributed_usage)}",
        f"CURRENT {summary.window_hours:g}H USED       {current_used}",
        f"WINDOW START          {summary.current_window_start or 'UNKNOWN'}",
        f"WINDOW END            {summary.current_window_end or 'UNKNOWN'}",
        f"UNASSIGNED SHOTS      {', '.join(summary.unassigned_shot_ids) or 'none'}",
    ]
    if summary.metric_kind == "internal_inference":
        lines.extend((f"TOTAL INFERENCE       {_fmt(summary.total_inference)}",
                      f"TOTAL USED            {_fmt(summary.total_used)} ({_pct(summary.total_used_percent)})"))
    if not summary.shots:
        lines.append("NO SHOT DATA")
    for shot in summary.shots:
        methods = sorted({row.source_method or "UNKNOWN" for row in shot.observations})
        lines.append(
            f"{shot.shot_id}: metric={shot.metric_kind or 'UNKNOWN'} unit={shot.delta_unit or 'UNKNOWN'} "
            f"display_delta={_fmt(shot.display_delta)} display_status={shot.display_status} "
            f"used={_fmt(shot.attributed_usage)} status={shot.status} "
            f"sources={','.join(methods)} reasons={','.join(shot.reasons) or 'none'}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m vector_cannon.inference_meter")
    parser.add_argument("--snapshot-file", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    record = sub.add_parser("record", help="Record an observation with explicit source and units.")
    record.add_argument("--launcher", required=True)
    record.add_argument("--shot", required=True)
    record.add_argument("--phase", required=True, choices=sorted(VALID_PHASE))
    record.add_argument("--total", type=float, default=None)
    record.add_argument("--remaining", type=float, default=None)
    record.add_argument("--used", type=float, default=None)
    record.add_argument("--status", choices=sorted(VALID_STATUS), default="MEASURED")
    record.add_argument("--note", default="")
    record.add_argument("--timestamp", "--observed-at", dest="timestamp", default=None)
    record.add_argument("--recorded-at", default=None)
    for name in METADATA_FIELDS:
        choices = (sorted(VALID_METRIC) if name == "metric_kind" else sorted(VALID_SOURCE)
                   if name == "source_method" else ["yes", "no", "unknown"]
                   if name in {"concurrent_usage", "reset_detected"} else None)
        record.add_argument("--" + name.replace("_", "-"), choices=choices, default=None)
    console = sub.add_parser("console", help="Render observations and explicit reporting windows.")
    console.add_argument("--launcher", required=True)
    console.add_argument("--window-hours", type=float, default=5.0)
    console.add_argument("--window-anchor", default=None, help="Explicit reporting-window start with timezone.")
    console.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "record":
        metadata = {name: getattr(args, name) for name in METADATA_FIELDS}
        for name in ("concurrent_usage", "reset_detected"):
            metadata[name] = {"yes": True, "no": False}.get(metadata[name])
        snapshot = record_snapshot(
            launcher=args.launcher, shot_id=args.shot, phase=args.phase,
            limit_value=args.total, remaining_value=args.remaining, used_value=args.used,
            status=args.status, note=args.note, timestamp=args.timestamp, recorded_at=args.recorded_at,
            path=args.snapshot_file, **metadata,
        )
        print(json.dumps(asdict(snapshot), ensure_ascii=False, allow_nan=False, indent=2))
        return 0
    summary = summarize_inference(load_snapshots(args.snapshot_file), launcher=args.launcher,
                                  window_hours=args.window_hours, window_anchor=args.window_anchor)
    if args.json:
        print(json.dumps(asdict(summary), ensure_ascii=False, allow_nan=False, indent=2))
    else:
        print(render_console(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
