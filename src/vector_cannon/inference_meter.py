from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

VALID_STATUS = {"MEASURED", "DERIVED", "ESTIMATED", "UNKNOWN"}
VALID_PHASE = {"start", "end", "checkpoint"}


@dataclass(frozen=True)
class InferenceSnapshot:
    timestamp: str
    launcher: str
    shot_id: str
    phase: str
    total_inference: float | None
    remaining_inference: float | None
    status: str
    note: str = ""


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


@dataclass(frozen=True)
class InferenceWindowUsage:
    window_start: str
    window_end: str
    inference_used: float | None
    known_used: float
    shot_count: int
    unknown_shots: int
    status: str


@dataclass(frozen=True)
class InferenceBudgetSummary:
    launcher: str
    total_inference: float | None
    latest_remaining: float | None
    total_used: float | None
    total_used_percent: float | None
    current_window_used: float | None
    current_window_known_used: float
    current_window_start: str | None
    current_window_end: str | None
    shots: tuple[ShotInferenceUsage, ...]
    windows: tuple[InferenceWindowUsage, ...]
    status: str


def default_snapshot_path() -> Path:
    return Path.home() / ".vector-cannon" / "inference" / "snapshots.jsonl"


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        raise ValueError("inference values cannot be negative")
    return parsed


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamp(value: str | None = None) -> str:
    return (_parse_time(value) if value else datetime.now(timezone.utc)).isoformat()


def record_snapshot(
    *,
    launcher: str,
    shot_id: str,
    phase: str,
    total_inference: float | None = None,
    remaining_inference: float | None = None,
    status: str = "MEASURED",
    note: str = "",
    timestamp: str | None = None,
    path: Path | None = None,
) -> InferenceSnapshot:
    launcher = launcher.strip()
    shot_id = shot_id.strip()
    phase = phase.strip().lower()
    status = status.strip().upper()
    if not launcher:
        raise ValueError("launcher must not be empty")
    if not shot_id:
        raise ValueError("shot_id must not be empty")
    if phase not in VALID_PHASE:
        raise ValueError(f"phase must be one of: {', '.join(sorted(VALID_PHASE))}")
    if status not in VALID_STATUS:
        raise ValueError(f"status must be one of: {', '.join(sorted(VALID_STATUS))}")

    total = _number(total_inference)
    remaining = _number(remaining_inference)
    if total is not None and remaining is not None and remaining > total:
        raise ValueError("remaining_inference cannot exceed total_inference")
    if status != "UNKNOWN" and total is None and remaining is None:
        raise ValueError("non-UNKNOWN snapshot requires total_inference or remaining_inference")

    snapshot = InferenceSnapshot(
        timestamp=_timestamp(timestamp),
        launcher=launcher,
        shot_id=shot_id,
        phase=phase,
        total_inference=total,
        remaining_inference=remaining,
        status=status,
        note=note,
    )
    target = (path or default_snapshot_path()).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(snapshot), ensure_ascii=False) + "\n")
    return snapshot


def load_snapshots(path: Path | None = None) -> list[InferenceSnapshot]:
    target = (path or default_snapshot_path()).expanduser()
    if not target.exists():
        return []
    rows: list[InferenceSnapshot] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            status = str(payload.get("status") or "UNKNOWN").upper()
            phase = str(payload.get("phase") or "checkpoint").lower()
            if status not in VALID_STATUS or phase not in VALID_PHASE:
                continue
            rows.append(
                InferenceSnapshot(
                    timestamp=_timestamp(str(payload.get("timestamp") or "")),
                    launcher=str(payload.get("launcher") or "unknown"),
                    shot_id=str(payload.get("shot_id") or "unknown"),
                    phase=phase,
                    total_inference=_number(payload.get("total_inference")),
                    remaining_inference=_number(payload.get("remaining_inference")),
                    status=status,
                    note=str(payload.get("note") or ""),
                )
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    rows.sort(key=lambda row: row.timestamp)
    return rows


def _weakest_status(statuses: Iterable[str]) -> str:
    rank = {"MEASURED": 0, "DERIVED": 1, "ESTIMATED": 2, "UNKNOWN": 3}
    values = [value if value in rank else "UNKNOWN" for value in statuses]
    return max(values, key=lambda value: rank[value]) if values else "UNKNOWN"


def _shot_usage(shot_id: str, rows: list[InferenceSnapshot]) -> ShotInferenceUsage:
    rows = sorted(rows, key=lambda row: row.timestamp)
    starts = [row for row in rows if row.phase == "start"]
    ends = [row for row in rows if row.phase == "end"]
    start = starts[0] if starts else rows[0]
    end = ends[-1] if ends else None

    totals = [row.total_inference for row in rows if row.total_inference is not None]
    total = totals[-1] if totals else None
    start_remaining = start.remaining_inference
    end_remaining = end.remaining_inference if end is not None else None
    used = None
    percent = None
    statuses = [row.status for row in rows]

    if end is not None and start_remaining is not None and end_remaining is not None:
        used = max(0.0, start_remaining - end_remaining)
        statuses.append("DERIVED")
        if total and total > 0:
            percent = used / total * 100.0
    else:
        statuses.append("UNKNOWN")

    return ShotInferenceUsage(
        shot_id=shot_id,
        launcher=start.launcher,
        started_at=start.timestamp if starts else None,
        ended_at=end.timestamp if end is not None else None,
        total_inference=total,
        start_remaining=start_remaining,
        end_remaining=end_remaining,
        inference_used=used,
        consumption_percent=percent,
        status=_weakest_status(statuses),
    )


def _window_usage(
    shots: tuple[ShotInferenceUsage, ...],
    *,
    window_hours: float,
    anchor: datetime,
    anchor_status: str,
) -> tuple[InferenceWindowUsage, ...]:
    buckets: dict[int, list[ShotInferenceUsage]] = {}
    width_seconds = window_hours * 3600.0
    for shot in shots:
        if shot.started_at is None:
            continue
        started = _parse_time(shot.started_at)
        index = math.floor((started - anchor).total_seconds() / width_seconds)
        buckets.setdefault(index, []).append(shot)

    windows: list[InferenceWindowUsage] = []
    for index in sorted(buckets):
        window_start = anchor + timedelta(hours=window_hours * index)
        window_end = window_start + timedelta(hours=window_hours)
        bucket = buckets[index]
        known = sum(shot.inference_used or 0.0 for shot in bucket if shot.inference_used is not None)
        unknown = sum(1 for shot in bucket if shot.inference_used is None)
        used = known if unknown == 0 else None
        statuses = [anchor_status] + [shot.status for shot in bucket]
        if unknown:
            statuses.append("UNKNOWN")
        else:
            statuses.append("DERIVED")
        windows.append(
            InferenceWindowUsage(
                window_start=window_start.isoformat(),
                window_end=window_end.isoformat(),
                inference_used=used,
                known_used=known,
                shot_count=len(bucket),
                unknown_shots=unknown,
                status=_weakest_status(statuses),
            )
        )
    return tuple(windows)


def summarize_inference(
    snapshots: Iterable[InferenceSnapshot],
    *,
    launcher: str,
    window_hours: float = 5.0,
    window_anchor: str | None = None,
) -> InferenceBudgetSummary:
    if window_hours <= 0:
        raise ValueError("window_hours must be positive")
    rows = [row for row in snapshots if row.launcher == launcher]
    rows.sort(key=lambda row: row.timestamp)
    if not rows:
        return InferenceBudgetSummary(
            launcher, None, None, None, None, None, 0.0, None, None, (), (), "UNKNOWN"
        )

    grouped: dict[str, list[InferenceSnapshot]] = {}
    for row in rows:
        grouped.setdefault(row.shot_id, []).append(row)
    shot_rows = tuple(_shot_usage(shot_id, grouped[shot_id]) for shot_id in grouped)

    totals = [row.total_inference for row in rows if row.total_inference is not None]
    total = totals[-1] if totals else None
    remainings = [row.remaining_inference for row in rows if row.remaining_inference is not None]
    latest_remaining = remainings[-1] if remainings else None

    total_used = None
    total_used_percent = None
    if total is not None and latest_remaining is not None:
        total_used = max(0.0, total - latest_remaining)
        if total > 0:
            total_used_percent = total_used / total * 100.0

    if window_anchor is not None:
        anchor = _parse_time(window_anchor)
        anchor_status = "DERIVED"
    else:
        shot_starts = [_parse_time(shot.started_at) for shot in shot_rows if shot.started_at is not None]
        anchor = min(shot_starts) if shot_starts else _parse_time(rows[0].timestamp)
        anchor_status = "ESTIMATED"

    windows = _window_usage(
        shot_rows,
        window_hours=window_hours,
        anchor=anchor,
        anchor_status=anchor_status,
    )
    current = windows[-1] if windows else None

    statuses = [row.status for row in rows] + [shot.status for shot in shot_rows]
    if total_used is not None:
        statuses.append("DERIVED")
    else:
        statuses.append("UNKNOWN")
    if current is not None:
        statuses.append(current.status)

    return InferenceBudgetSummary(
        launcher=launcher,
        total_inference=total,
        latest_remaining=latest_remaining,
        total_used=total_used,
        total_used_percent=total_used_percent,
        current_window_used=current.inference_used if current else None,
        current_window_known_used=current.known_used if current else 0.0,
        current_window_start=current.window_start if current else None,
        current_window_end=current.window_end if current else None,
        shots=shot_rows,
        windows=windows,
        status=_weakest_status(statuses),
    )


def _fmt(value: float | None) -> str:
    return "UNKNOWN" if value is None else f"{value:.3f}".rstrip("0").rstrip(".")


def _pct(value: float | None) -> str:
    return "UNKNOWN" if value is None else f"{value:.2f}%"


def render_console(summary: InferenceBudgetSummary) -> str:
    current_used = _fmt(summary.current_window_used)
    if summary.current_window_used is None and summary.current_window_known_used > 0:
        current_used += f" (known >= {_fmt(summary.current_window_known_used)})"
    lines = [
        "VECTOR CANNON / INFERENCE BUDGET",
        "────────────────────────────────",
        f"LAUNCHER              {summary.launcher}",
        f"STATUS                {summary.status}",
        "",
        f"TOTAL INFERENCE       {_fmt(summary.total_inference)}",
        f"TOTAL USED            {_fmt(summary.total_used)} ({_pct(summary.total_used_percent)})",
        f"REMAINING             {_fmt(summary.latest_remaining)}",
        "",
        f"CURRENT 5H USED       {current_used}",
        f"5H WINDOW START       {summary.current_window_start or 'UNKNOWN'}",
        f"5H WINDOW END         {summary.current_window_end or 'UNKNOWN'}",
        "────────────────────────────────",
    ]
    if not summary.shots:
        lines.append("NO SHOT DATA")
    else:
        for shot in summary.shots:
            lines.append(
                f"{shot.shot_id}: used={_fmt(shot.inference_used)} "
                f"start={_fmt(shot.start_remaining)} end={_fmt(shot.end_remaining)} "
                f"status={shot.status}"
            )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m vector_cannon.inference_meter")
    parser.add_argument("--snapshot-file", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    record = sub.add_parser("record", help="Record a launcher inference-budget snapshot.")
    record.add_argument("--launcher", required=True)
    record.add_argument("--shot", required=True)
    record.add_argument("--phase", required=True, choices=sorted(VALID_PHASE))
    record.add_argument("--total", type=float, default=None)
    record.add_argument("--remaining", type=float, default=None)
    record.add_argument("--status", choices=sorted(VALID_STATUS), default="MEASURED")
    record.add_argument("--note", default="")
    record.add_argument("--timestamp", default=None)

    console = sub.add_parser("console", help="Render total and five-hour inference consumption.")
    console.add_argument("--launcher", required=True)
    console.add_argument("--window-hours", type=float, default=5.0)
    console.add_argument(
        "--window-anchor",
        default=None,
        help="Known quota-window start as ISO-8601. If omitted, the first shot is an ESTIMATED anchor.",
    )
    console.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "record":
        snapshot = record_snapshot(
            launcher=args.launcher,
            shot_id=args.shot,
            phase=args.phase,
            total_inference=args.total,
            remaining_inference=args.remaining,
            status=args.status,
            note=args.note,
            timestamp=args.timestamp,
            path=args.snapshot_file,
        )
        print(json.dumps(asdict(snapshot), ensure_ascii=False, indent=2))
        return 0

    summary = summarize_inference(
        load_snapshots(args.snapshot_file),
        launcher=args.launcher,
        window_hours=args.window_hours,
        window_anchor=args.window_anchor,
    )
    if args.json:
        print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
    else:
        print(render_console(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
