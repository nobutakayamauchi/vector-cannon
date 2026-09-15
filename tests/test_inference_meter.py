from pathlib import Path

from vector_cannon.inference_meter import (
    load_snapshots,
    record_snapshot,
    render_console,
    summarize_inference,
)


def test_inference_meter_calculates_total_and_per_shot_usage(tmp_path: Path) -> None:
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
        timestamp="2026-09-15T05:00:00+00:00",
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
        timestamp="2026-09-15T11:00:00+00:00",
        path=path,
    )

    summary = summarize_inference(load_snapshots(path), launcher="astra")

    assert summary.total_inference == 1000
    assert summary.latest_remaining == 600
    assert summary.total_used == 400
    assert summary.total_used_percent == 40
    assert summary.latest_shot_used == 220
    assert summary.latest_shot_percent == 22
    assert [shot.inference_used for shot in summary.shots] == [180, 220]
    assert summary.status == "DERIVED"

    console = render_console(summary)
    assert "TOTAL INFERENCE       1000" in console
    assert "TOTAL USED            400 (40.00%)" in console
    assert "LATEST 5H/SHOT USED   220 (22.00%)" in console


def test_missing_end_snapshot_stays_unknown_instead_of_zero(tmp_path: Path) -> None:
    path = tmp_path / "snapshots.jsonl"
    record_snapshot(
        launcher="astra",
        shot_id="shot-001",
        phase="start",
        total_inference=1000,
        remaining_inference=1000,
        path=path,
    )

    summary = summarize_inference(load_snapshots(path), launcher="astra")

    assert summary.total_inference == 1000
    assert summary.latest_remaining == 1000
    assert summary.total_used == 0
    assert summary.latest_shot_used == 0
    assert summary.shots[0].ended_at is None
