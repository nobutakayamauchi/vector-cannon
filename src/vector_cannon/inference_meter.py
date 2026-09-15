from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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
class InferenceBudgetSummary:
    launcher: str
    total_inference: float | None
    latest_remaining: float | None
    total_used: float | None
    total_used_percent: float | None
    latest_shot_used: float | None
    latest_shot_percent: float | None
    shots: tuple[ShotInferenceUsage, ...]
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


def _timestamp(value: str | None = None) -> str:
    if value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


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
    starts = [row for row in rows if row.phase == "start"]
    ends = [row for row in rows if row.phase == "end"]
    start = starts[0] if starts else rows[0]
    end = ends[-1] if ends else rows[-1]

    totals = [row.total_inference for row in rows if row.total_inference is not None]
    total = totals[-1] if totals else None
    start_remaining = start.remaining_inference
    end_remaining = end.remaining_inference
    used = None
    percent = None
    statuses = [row.status for row in rows]

    if start_remaining is not None and end_remaining is not None:
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
        ended_at=end.timestamp if ends else None,
        total_inference=total,
        start_remaining=start_remaining,
        end_remaining=end_remaining,
        inference_used=used,
        consumption_percent=percent,
        status=_weakest_status(statuses),
    )


def summarize_inference(
    snapshots: Iterable[InferenceSnapshot], *, launcher: str
) -> InferenceBudgetSummary:
    rows = [row for row in snapshots if row.launcher == launcher]
    rows.sort(key=lambda row: row.timestamp)
    if not rows:
        return InferenceBudgetSummary(launcher, None, None, None, None, None, None, (), "UNKNOWN")

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

    latest = shot_rows[-1] if shot_rows else None
    statuses = [row.status for row in rows]
    if total_used is not None:
        statuses.append("DERIVED")
    else:
        statuses.append("UNKNOWN")

    return InferenceBudgetSummary(
        launcher=launcher,
        total_inference=total,
        latest_remaining=latest_remaining,
        total_used=total_used,
        total_used_percent=total_used_percent,
        latest_shot_used=latest.inference_used if latest else None,
        latest_shot_percent=latest.consumption_percent if latest else None,
        shots=shot_rows,
        status=_weakest_status(statuses),
    )


def _fmt(value: float | None) -> str:
    return "UNKNOWN" if value is None else f"{value:.3f}".rstrip("0").rstrip(".")


def _pct(value: float | None) -> str:
    return "UNKNOWN" if value is None else f"{value:.2f}%"


def render_console(summary: InferenceBudgetSummary) -> str:
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
        f"LATEST 5H/SHOT USED   {_fmt(summary.latest_shot_used)} ({_pct(summary.latest_shot_percent)})",
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

    console = sub.add_parser("console", help="Render total and per-shot inference consumption.")
    console.add_argument("--launcher", required=True)
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

    summary = summarize_inference(load_snapshots(args.snapshot_file), launcher=args.launcher)
    if args.json:
        print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
    else:
        print(render_console(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
