from pathlib import Path

from vector_cannon.inference_meter import (
    load_snapshots,
    record_snapshot,
    render_console,
    summarize_inference,
)


def test_inference_meter_calculates_total_and_five_hour_usage(tmp_path: Path) -> None:
    path = tmp_path / "snapshots.jsonl"
    record_snapshot(
        launcher="astra",
        shot_id="shot-001",
        phase="start",
        total_inference=1000,
        remaining_inference=1000,
        timestamp="2026-09-15T00:00:00+00:00",
        path=path,
    )
    record_snapshot(
        launcher="astra",
        shot_id="shot-001",
        phase="end",
        total_inference=1000,
        remaining_inference=820,
        timestamp="2026-09-15T00:08:00+00:00",
        path=path,
    )
    record_snapshot(
        launcher="astra",
        shot_id="shot-002",
        phase="start",
        total_inference=1000,
        remaining_inference=820,
        timestamp="2026-09-15T06:00:00+00:00",
        path=path,
    )
    record_snapshot(
        launcher="astra",
        shot_id="shot-002",
        phase="end",
        total_inference=1000,
        remaining_inference=600,
        timestamp="2026-09-15T06:07:00+00:00",
        path=path,
    )

    summary = summarize_inference(
        load_snapshots(path),
        launcher="astra",
        window_anchor="2026-09-15T00:00:00+00:00",
    )

    assert summary.total_inference == 1000
    assert summary.latest_remaining == 600
    assert summary.total_used == 400
    assert summary.total_used_percent == 40
    assert [shot.inference_used for shot in summary.shots] == [180, 220]
    assert [window.inference_used for window in summary.windows] == [180, 220]
    assert summary.current_window_used == 220
    assert summary.current_window_known_used == 220
    assert summary.status == "DERIVED"

    console = render_console(summary)
    assert "TOTAL INFERENCE       1000" in console
    assert "TOTAL USED            400 (40.00%)" in console
    assert "CURRENT 5H USED       220" in console


def test_missing_end_snapshot_stays_unknown_instead_of_zero(tmp_path: Path) -> None:
    path = tmp_path / "snapshots.jsonl"
    record_snapshot(
        launcher="astra",
        shot_id="shot-001",
        phase="start",
        total_inference=1000,
        remaining_inference=1000,
        timestamp="2026-09-15T00:00:00+00:00",
        path=path,
    )

    summary = summarize_inference(
        load_snapshots(path),
        launcher="astra",
        window_anchor="2026-09-15T00:00:00+00:00",
    )

    assert summary.total_inference == 1000
    assert summary.latest_remaining == 1000
    assert summary.total_used == 0
    assert summary.current_window_used is None
    assert summary.current_window_known_used == 0
    assert summary.shots[0].inference_used is None
    assert summary.shots[0].ended_at is None
    assert summary.windows[0].inference_used is None
    assert summary.windows[0].unknown_shots == 1
    assert summary.status == "UNKNOWN"


def test_unknown_shot_does_not_hide_known_lower_bound(tmp_path: Path) -> None:
    path = tmp_path / "snapshots.jsonl"
    record_snapshot(
        launcher="astra",
        shot_id="shot-001",
        phase="start",
        total_inference=1000,
        remaining_inference=1000,
        timestamp="2026-09-15T00:00:00+00:00",
        path=path,
    )
    record_snapshot(
        launcher="astra",
        shot_id="shot-001",
        phase="end",
        total_inference=1000,
        remaining_inference=900,
        timestamp="2026-09-15T00:05:00+00:00",
        path=path,
    )
    record_snapshot(
        launcher="astra",
        shot_id="shot-002",
        phase="start",
        total_inference=1000,
        remaining_inference=900,
        timestamp="2026-09-15T01:00:00+00:00",
        path=path,
    )

    summary = summarize_inference(
        load_snapshots(path),
        launcher="astra",
        window_anchor="2026-09-15T00:00:00+00:00",
    )

    assert summary.current_window_used is None
    assert summary.current_window_known_used == 100
    assert "known >= 100" in render_console(summary)
