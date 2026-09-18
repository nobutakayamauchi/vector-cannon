from __future__ import annotations

import json
import socket
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from vector_cannon.inference_meter import (
    load_snapshots,
    main,
    record_snapshot,
    render_console,
    summarize_inference,
)

START = "2026-09-15T00:00:00+00:00"
END = "2026-09-15T00:05:00+00:00"
METADATA = {
    "metric_kind": "internal_inference",
    "unit": "reasoning_tokens",
    "source_method": "fixture",
    "source_ref": "fixture://W02/observations",
    "source_scale": "fixture-reasoning-count-v1",
    "provider": "fixture-provider",
    "plan_id": "fixture-plan",
    "scope_id": "shared-scope-1",
    "period_id": "fixture-period-1",
    "period_start": START,
    "period_end": "2026-09-15T12:00:00+00:00",
    "reset_id": "fixture-reset-1",
    "concurrent_usage": False,
    "reset_detected": False,
    "exclusive_evidence": "fixture://W02/exclusive-session-and-reset-observation",
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("observation tests must not contact external services")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)


def observe(path: Path, phase: str, remaining=1000, **changes):
    options = dict(
        METADATA, launcher="astra", shot_id="shot-001", phase=phase,
        limit_value=1000, remaining_value=remaining,
        timestamp=START if phase == "start" else END,
        recorded_at="2026-09-15T00:10:00+00:00", path=path,
    )
    options.update(changes)
    return record_snapshot(**options)


def pair(path: Path, *, start=1000, end=900, start_changes=None, end_changes=None, **common):
    first = observe(path, "start", start, **(common | (start_changes or {})))
    last = observe(path, "end", end, **(common | (end_changes or {})))
    return [first, last]


def summarize(rows, **changes):
    return summarize_inference(rows, **(dict(launcher="astra", window_anchor=START) | changes))


def test_inference_meter_calculates_total_and_five_hour_usage(tmp_path: Path) -> None:
    """O10/O12: retain the original positive regression with explicit fixture evidence."""
    path = tmp_path / "snapshots.jsonl"
    pair(path, end=820)
    pair(path, shot_id="shot-002", start=820, end=600,
         start_changes={"timestamp": "2026-09-15T06:00:00+00:00"},
         end_changes={"timestamp": "2026-09-15T06:07:00+00:00"})
    summary = summarize(load_snapshots(path))
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
    """O5/O12: the start balance is not evidence of a free unfinished shot."""
    path = tmp_path / "snapshots.jsonl"
    observe(path, "start")
    summary = summarize(load_snapshots(path))
    assert summary.total_inference == 1000
    assert summary.latest_remaining == 1000
    assert summary.total_used is None  # Previously asserted 0: that was not shot consumption.
    assert summary.total_attributed_usage is None
    assert summary.current_window_used is None
    assert summary.current_window_known_used == 0
    assert summary.shots[0].inference_used is None
    assert summary.shots[0].ended_at is None
    assert "MISSING_END" in summary.shots[0].reasons
    assert summary.windows[0].inference_used is None
    assert summary.windows[0].unknown_shots == 1
    assert summary.status == "UNKNOWN"
    assert "TOTAL SHOT USED       UNKNOWN" in render_console(summary)
    assert "used=UNKNOWN" in render_console(summary)


def test_unknown_shot_does_not_hide_known_lower_bound(tmp_path: Path) -> None:
    """O11: keep only the complete non-overlapping shot as a known lower bound."""
    path = tmp_path / "snapshots.jsonl"
    pair(path)
    observe(path, "start", 900, shot_id="shot-002", timestamp="2026-09-15T01:00:00+00:00")
    summary = summarize(load_snapshots(path))
    assert summary.current_window_used is None
    assert summary.current_window_known_used == 100
    assert summary.known_attributed_usage == 100
    assert summary.total_attributed_usage is None
    assert "known >= 100" in render_console(summary)


def test_o1_quota_percentage_points_are_not_inference_or_money(tmp_path: Path):
    rows = pair(tmp_path / "quota.jsonl", start=80, end=70, limit_value=100,
                metric_kind="quota", unit="percent", source_scale="fixture-quota-percent")
    summary = summarize(rows)
    shot = summary.shots[0]
    assert shot.display_delta == shot.attributed_usage == 10
    assert shot.unit == "percent" and shot.delta_unit == "percentage_points"
    assert shot.status == "DERIVED"
    assert shot.inference_used is shot.total_inference is shot.consumption_percent is None
    assert summary.total_used is summary.total_inference is summary.latest_remaining is None
    assert summary.current_window_used is summary.current_window_known_used is None
    assert summary.windows[0].attributed_usage == 10
    assert summary.windows[0].delta_unit == "percentage_points"
    assert all(row.total_inference is None and row.remaining_inference is None for row in rows)
    text = render_console(summary)
    assert "percentage_points" in text and "TOTAL INFERENCE" not in text and "USD" not in text


@pytest.mark.parametrize(("change", "reason"), [
    ({"metric_kind": "quota"}, "METRIC_CHANGED"),
    ({"unit": "output_tokens"}, "UNIT_CHANGED"),
    ({"scope_id": "other-scope"}, "SCOPE_CHANGED"),
    ({"provider": "other-provider"}, "PROVIDER_CHANGED"),
    ({"source_scale": "different-meter-definition"}, "SOURCE_SCALE_CHANGED"),
])
def test_o2_incompatible_observations_keep_raw_values(tmp_path: Path, change, reason):
    rows = pair(tmp_path / "mixed.jsonl", end_changes=change)
    summary = summarize(rows)
    shot = summary.shots[0]
    assert shot.display_delta is shot.attributed_usage is None
    assert shot.status == "UNKNOWN" and reason in shot.reasons
    assert [row.remaining_value for row in shot.observations] == [1000, 900]
    assert summary.total_attributed_usage is summary.known_attributed_usage is None
    assert "GROUPED_ACCOUNTING_REQUIRED" in summary.reasons


@pytest.mark.parametrize("field", [
    "metric_kind", "unit", "source_scale", "provider", "scope_id", "plan_id", "period_id",
    "period_start", "period_end", "reset_id", "source_ref", "source_method",
])
@pytest.mark.parametrize("missing", [None, "UNKNOWN"])
def test_o2_unknown_identifiers_do_not_establish_equality(tmp_path: Path, field, missing):
    rows = pair(tmp_path / "unknown.jsonl", **{field: missing})
    shot = summarize(rows).shots[0]
    assert shot.display_delta is None and shot.attributed_usage is None
    assert shot.status == "UNKNOWN" and shot.reasons
    assert [row.remaining_value for row in shot.observations] == [1000, 900]


def test_o3_metadata_roundtrip_and_timezones(tmp_path: Path):
    path = tmp_path / "roundtrip.jsonl"
    rows = pair(path, source_method="manual", note="transcribed source display",
                start_changes={"timestamp": "2026-09-15T09:00:00+09:00", "source_ref": "manual://start"},
                end_changes={"source_ref": "manual://end", "used_value": 100})
    loaded = load_snapshots(path)
    assert loaded == rows
    assert loaded[0].timestamp == START
    assert loaded[0].recorded_at == "2026-09-15T00:10:00+00:00"
    for name, value in METADATA.items():
        if name not in {"source_method", "source_ref"}:
            assert getattr(loaded[0], name) == value
    assert loaded[1].used_value == 100
    assert loaded[1].limit_value == 1000 and loaded[1].remaining_value == 900
    shot = summarize(loaded).shots[0]
    assert shot.attributed_usage == 100
    assert [row.source_ref for row in shot.observations] == ["manual://start", "manual://end"]
    assert "sources=manual" in render_console(summarize(loaded))


@pytest.mark.parametrize("timestamp", [None, "2026-09-15T00:00:00", "not-a-time"])
def test_o3_missing_or_naive_time_is_never_filled_from_clock(tmp_path: Path, timestamp):
    path = tmp_path / "time.jsonl"
    row = observe(path, "start", timestamp=timestamp)
    loaded = load_snapshots(path)[0]
    assert row.timestamp is loaded.timestamp is None
    assert row.recorded_at is not None
    assert loaded.status == "UNKNOWN"
    assert loaded.remaining_value == 1000
    if timestamp is not None:
        assert loaded.raw_fields["timestamp"] == timestamp
    summary = summarize([loaded])
    assert summary.unassigned_shot_ids == ("shot-001",)
    assert "WINDOW_ALLOCATION_UNKNOWN" in summary.reasons


def test_o4_legacy_record_is_readable_without_rewrite_or_invented_metadata(tmp_path: Path):
    path = tmp_path / "legacy.jsonl"
    payload = {"launcher": "astra", "shot_id": "old", "phase": "start", "status": "MEASURED",
               "total_inference": 1000, "remaining_inference": 800}
    original = json.dumps(payload) + "\n"
    path.write_text(original, encoding="utf-8")
    row = load_snapshots(path)[0]
    assert row.limit_value == 1000 and row.remaining_value == 800
    assert row.total_inference is row.remaining_inference is None
    assert row.raw_fields["remaining_inference"] == 800
    assert row.metric_kind is row.unit is row.timestamp is row.recorded_at is None
    assert row.plan_id is row.period_id is row.source_ref is row.reset_id is None
    assert row.status == "UNKNOWN"
    summary = summarize([row])
    assert summary.total_attributed_usage is None and summary.unassigned_shot_ids == ("old",)
    assert len(summary.shots[0].observations) == 1
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf"), -1, True, "bad"])
def test_o4_invalid_numbers_preserve_other_observations(tmp_path: Path, invalid):
    path = tmp_path / "invalid.jsonl"
    rows = pair(path, end=invalid)
    loaded = load_snapshots(path)
    assert loaded[1].remaining_value is None and loaded[1].status == "UNKNOWN"
    assert loaded[0].remaining_value == 1000 and loaded[1].limit_value == 1000
    assert "remaining_value" in loaded[1].raw_fields
    assert "INVALID_REMAINING_VALUE" in loaded[1].reasons
    assert summarize(loaded).shots[0].attributed_usage is None
    json.dumps(asdict(summarize(rows)), allow_nan=False)


def test_o4_zero_missing_and_reported_unknown_are_distinct(tmp_path: Path):
    path = tmp_path / "zero.jsonl"
    rows = pair(path, start=0, end=0, limit_value=0)
    summary = summarize(rows)
    assert summary.total_used == 0 and summary.status == "DERIVED"
    assert summary.shots[0].attributed_usage == 0
    assert summary.shots[0].consumption_percent is None
    missing = pair(tmp_path / "missing.jsonl", end=None)
    assert summarize(missing).shots[0].attributed_usage is None
    unknown = pair(tmp_path / "reported-unknown.jsonl", end_changes={"status": "UNKNOWN"})
    shot = summarize(unknown).shots[0]
    assert shot.attributed_usage is shot.display_delta is None
    assert shot.observations[-1].remaining_value == 900
    assert "UNKNOWN_OBSERVATION" in shot.reasons


def test_o4_invalid_json_line_is_reported_instead_of_silently_dropped(tmp_path: Path):
    path = tmp_path / "broken.jsonl"
    observe(path, "start")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{broken end record\n")
    with pytest.raises(ValueError, match="line 2"):
        load_snapshots(path)


@pytest.mark.parametrize(("phases", "reason"), [
    (["start"], "MISSING_END"), (["end"], "MISSING_START"),
    (["checkpoint", "end"], "MISSING_START"), (["checkpoint"], "MISSING_START"),
])
def test_o5_missing_phases_keep_unassigned_records(tmp_path: Path, phases, reason):
    path = tmp_path / "phases.jsonl"
    rows = [observe(path, phase) for phase in phases]
    summary = summarize(rows)
    shot = summary.shots[0]
    assert shot.display_delta is shot.attributed_usage is None
    assert reason in shot.reasons and shot.status == "UNKNOWN"
    assert len(shot.observations) == len(phases)
    if "start" not in phases:
        assert shot.started_at is None
        assert "shot-001" in summary.unassigned_shot_ids
        assert "UNASSIGNED SHOTS      shot-001" in render_console(summary)
    if phases == ["checkpoint"]:
        assert "MISSING_END" in shot.reasons


def test_o5_no_observations_is_unknown():
    summary = summarize([])
    assert summary.status == "UNKNOWN" and summary.total_attributed_usage is None
    assert summary.known_attributed_usage is None
    assert "NO_OBSERVATIONS" in summary.reasons


@pytest.mark.parametrize("end_time", [START, "2026-09-14T23:59:00+00:00"])
def test_o6_reversed_or_equal_times_are_unknown(tmp_path: Path, end_time):
    rows = pair(tmp_path / "reverse.jsonl", end_changes={"timestamp": end_time})
    shot = summarize(rows).shots[0]
    assert shot.attributed_usage is shot.display_delta is None
    assert "TIME_ORDER_INVALID" in shot.reasons


@pytest.mark.parametrize("phase", ["start", "end"])
def test_o6_ambiguous_pairs_are_not_selected(tmp_path: Path, phase):
    path = tmp_path / "ambiguous.jsonl"
    rows = pair(path)
    rows.append(observe(path, phase, 950, timestamp="2026-09-15T00:02:00+00:00"))
    shot = summarize(rows).shots[0]
    assert shot.attributed_usage is shot.display_delta is None
    assert f"AMBIGUOUS_{phase.upper()}" in shot.reasons
    assert len(shot.observations) == 3


@pytest.mark.parametrize(("changes", "reason"), [
    ({"concurrent_usage": True}, "CONCURRENT_USAGE"),
    ({"concurrent_usage": None}, "CONCURRENCY_UNKNOWN"),
    ({"exclusive_evidence": None}, "EXCLUSIVITY_UNPROVEN"),
    ({"exclusive_evidence": "UNKNOWN"}, "EXCLUSIVITY_UNPROVEN"),
])
def test_o7_display_delta_is_separate_from_shot_attribution(tmp_path: Path, changes, reason):
    rows = pair(tmp_path / "concurrent.jsonl", **changes)
    shot = summarize(rows).shots[0]
    assert shot.display_delta == 100 and shot.display_status == "DERIVED"
    assert shot.attributed_usage is shot.inference_used is None
    assert shot.status == "UNKNOWN" and reason in shot.reasons


@pytest.mark.parametrize("other_launcher", ["astra", "other-launcher"])
def test_o7_overlapping_shared_scope_blocks_double_attribution(tmp_path: Path, other_launcher):
    path = tmp_path / "overlap.jsonl"
    rows = pair(path)
    rows += pair(path, shot_id="shot-002", launcher=other_launcher, start=980, end=850,
                 start_changes={"timestamp": "2026-09-15T00:01:00+00:00"},
                 end_changes={"timestamp": "2026-09-15T00:06:00+00:00"})
    summary = summarize(rows)
    assert all(shot.attributed_usage is None for shot in summary.shots)
    assert all("CONCURRENT_USAGE" in shot.reasons for shot in summary.shots)
    assert summary.known_attributed_usage == 0 and summary.total_attributed_usage is None
    assert summary.current_window_known_used == 0
    assert summary.shots[0].display_delta == 100


def test_o7_incomplete_overlapping_shot_keeps_attribution_unknown(tmp_path: Path):
    path = tmp_path / "overlap-open.jsonl"
    rows = pair(path)
    rows.append(observe(path, "start", 980, shot_id="shot-002", timestamp="2026-09-15T00:01:00+00:00"))
    summary = summarize(rows)
    assert summary.known_attributed_usage == 0
    assert "OVERLAP_UNKNOWN" in summary.shots[0].reasons


@pytest.mark.parametrize(("change", "reason"), [
    ({"period_id": "period-2"}, "PERIOD_CHANGED"),
    ({"period_end": "2026-09-15T13:00:00+00:00"}, "PERIOD_CHANGED"),
    ({"plan_id": "plan-2"}, "PLAN_CHANGED"),
    ({"limit_value": 2000}, "LIMIT_CHANGED"),
])
def test_o8_period_plan_or_limit_change_blocks_subtraction(tmp_path: Path, change, reason):
    rows = pair(tmp_path / "change.jsonl", end_changes=change)
    summary = summarize(rows)
    shot = summary.shots[0]
    assert shot.display_delta is shot.attributed_usage is None
    assert reason in shot.reasons
    assert [row.remaining_value for row in shot.observations] == [1000, 900]
    assert summary.total_attributed_usage is None


def test_o8_period_boundary_is_not_prorated(tmp_path: Path):
    rows = pair(tmp_path / "boundary.jsonl",
                start_changes={"timestamp": "2026-09-15T11:59:00+00:00"},
                end_changes={"timestamp": METADATA["period_end"]})
    summary = summarize(rows)
    assert summary.shots[0].attributed_usage is None
    assert "PERIOD_BOUNDARY" in summary.shots[0].reasons
    assert summary.unassigned_shot_ids == ("shot-001",)
    assert summary.total_attributed_usage is None


@pytest.mark.parametrize(("change", "reason"), [
    ({"reset_detected": True}, "RESET_DETECTED"),
    ({"reset_detected": None}, "RESET_STATE_UNKNOWN"),
    ({"reset_id": "reset-2"}, "RESET_INTERVAL_CHANGED"),
    ({"reset_id": None}, "RESET_INTERVAL_UNKNOWN"),
])
def test_o9_reset_evidence_blocks_subtraction(tmp_path: Path, change, reason):
    rows = pair(tmp_path / "reset.jsonl", end_changes=change)
    shot = summarize(rows).shots[0]
    assert shot.display_delta is shot.attributed_usage is None
    assert reason in shot.reasons


def test_o9_increasing_remaining_is_not_clamped_to_zero(tmp_path: Path):
    rows = pair(tmp_path / "increase.jsonl", start=800, end=900)
    shot = summarize(rows).shots[0]
    assert shot.attributed_usage is shot.display_delta is None
    assert "REMAINING_INCREASED" in shot.reasons
    assert [row.remaining_value for row in shot.observations] == [800, 900]


@pytest.mark.parametrize(("changes", "remaining", "reason"), [
    ({"reset_detected": True}, 950, "RESET_DETECTED"),
    ({"reset_id": "intermediate-reset"}, 950, "RESET_INTERVAL_CHANGED"),
    ({"limit_value": 2000}, 950, "LIMIT_CHANGED"),
    ({"plan_id": "intermediate-plan"}, 950, "PLAN_CHANGED"),
    ({"period_id": "intermediate-period"}, 950, "PERIOD_CHANGED"),
    ({}, 1000, "REMAINING_INCREASED"),
])
def test_o9_checkpoints_participate_in_comparability(tmp_path: Path, changes, remaining, reason):
    path = tmp_path / "checkpoint.jsonl"
    rows = pair(path, start=980)
    rows.append(observe(path, "checkpoint", remaining,
                        timestamp="2026-09-15T00:02:00+00:00", **changes))
    shot = summarize(rows).shots[0]
    assert shot.attributed_usage is shot.display_delta is None
    assert reason in shot.reasons and len(shot.observations) == 3


def test_o10_internal_quantity_derived_with_explicit_manual_provenance(tmp_path: Path):
    rows = pair(tmp_path / "internal.jsonl", source_method="manual", source_ref="manual://meter-readings")
    summary = summarize(rows)
    shot = summary.shots[0]
    assert shot.attributed_usage == shot.inference_used == shot.display_delta == 100
    assert shot.status == shot.display_status == "DERIVED"
    assert shot.unit == shot.delta_unit == "reasoning_tokens"
    assert all(row.source_method == "manual" for row in shot.observations)
    assert all(row.source_ref == "manual://meter-readings" for row in shot.observations)
    text = render_console(summary)
    assert "sources=manual" in text and "automatic" not in text.lower()


def test_o11_estimated_difference_does_not_enter_known_lower_bound(tmp_path: Path):
    path = tmp_path / "estimated.jsonl"
    rows = pair(path)
    rows += pair(path, shot_id="estimated", start=900, end=800, status="ESTIMATED",
                 start_changes={"timestamp": "2026-09-15T01:00:00+00:00"},
                 end_changes={"timestamp": "2026-09-15T01:05:00+00:00"})
    summary = summarize(rows)
    estimated = summary.shots[1]
    assert estimated.display_delta == 100 and estimated.display_status == "ESTIMATED"
    assert estimated.attributed_usage is None and "ESTIMATED_OBSERVATION" in estimated.reasons
    assert summary.known_attributed_usage == summary.current_window_known_used == 100
    assert summary.total_attributed_usage is summary.current_window_used is None
    assert summary.status == "UNKNOWN"


@pytest.mark.parametrize("change", [
    {"unit": "output_tokens"}, {"period_id": "different-period"}, {"plan_id": "other-plan"},
    {"scope_id": "other-scope"}, {"metric_kind": "cost", "unit": "USD"},
])
def test_o12_mixed_summary_does_not_select_latest_or_sum_groups(tmp_path: Path, change):
    path = tmp_path / "groups.jsonl"
    rows = pair(path)
    rows += pair(path, shot_id="other", start=900, end=800,
                 start_changes={"timestamp": "2026-09-15T01:00:00+00:00"},
                 end_changes={"timestamp": "2026-09-15T01:05:00+00:00"}, **change)
    summary = summarize(rows)
    assert len(summary.shots) == 2
    assert summary.metric_kind is summary.unit is summary.limit_value is None
    assert summary.total_attributed_usage is summary.known_attributed_usage is None
    assert summary.latest_remaining_value is summary.latest_remaining is None
    assert summary.windows == () and len(summary.unassigned_shot_ids) == 2
    assert "GROUPED_ACCOUNTING_REQUIRED" in summary.reasons
    assert [shot.attributed_usage for shot in summary.shots] == [100, 100]


def test_o12_no_implicit_window_anchor_and_no_time_proration(tmp_path: Path):
    rows = pair(tmp_path / "window.jsonl",
                start_changes={"timestamp": "2026-09-15T04:59:00+00:00"},
                end_changes={"timestamp": "2026-09-15T05:01:00+00:00"})
    summary = summarize(rows)
    assert summary.shots[0].attributed_usage == 100
    assert summary.current_window_used is None and summary.windows == ()
    assert "WINDOW_ALLOCATION_UNKNOWN" in summary.reasons
    assert summary.unassigned_shot_ids == ("shot-001",)
    no_anchor = summarize(rows, window_anchor=None)
    assert no_anchor.windows == () and "WINDOW_ANCHOR_UNKNOWN" in no_anchor.reasons


def test_o12_cli_json_keeps_units_metadata_and_unknown(tmp_path: Path, capsys):
    path = tmp_path / "cli.jsonl"
    metadata = dict(METADATA, metric_kind="quota", unit="percent", source_scale="quota-percent",
                    source_method="manual")
    flags = []
    for name, value in metadata.items():
        if isinstance(value, bool):
            value = "yes" if value else "no"
        flags.extend(["--" + name.replace("_", "-"), value])
    for phase, value, timestamp in [("start", "80", START), ("end", "70", END)]:
        assert main(["--snapshot-file", str(path), "record", "--launcher", "astra", "--shot", "cli",
                     "--phase", phase, "--remaining", value, "--total", "100",
                     "--observed-at", timestamp, *flags]) == 0
        record = json.loads(capsys.readouterr().out)
        assert record["metric_kind"] == "quota" and record["source_method"] == "manual"
        assert record["recorded_at"] is not None
        assert record["total_inference"] is record["remaining_inference"] is None
    args = ["--snapshot-file", str(path), "console", "--launcher", "astra", "--window-anchor", START]
    assert main([*args, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["delta_unit"] == "percentage_points" and result["total_attributed_usage"] == 10
    assert result["total_used"] is None and result["shots"][0]["inference_used"] is None
    assert len(result["shots"][0]["observations"]) == 2
    assert main(args) == 0
    assert "sources=manual" in capsys.readouterr().out
    observe(path, "start", 60, shot_id="unfinished", timestamp="2026-09-15T01:00:00+00:00",
            limit_value=100, **metadata)
    assert main([*args, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["total_attributed_usage"] is None and "MISSING_END" in result["reasons"]
    assert result["shots"][1]["attributed_usage"] is None


def test_o12_currency_observation_is_not_internal_quantity(tmp_path: Path):
    row = observe(tmp_path / "cost.jsonl", "checkpoint", None, limit_value=None,
                  used_value=1.25, metric_kind="cost", unit="USD", source_scale="fixture-reported-usd")
    loaded = load_snapshots(tmp_path / "cost.jsonl")[0]
    assert loaded.used_value == 1.25 and loaded.unit == "USD" and loaded.metric_kind == "cost"
    assert loaded.total_inference is loaded.remaining_inference is None
    summary = summarize([row])
    assert summary.total_used is summary.total_attributed_usage is None
    assert summary.shots[0].observations[0].used_value == 1.25


def test_public_dataclass_values_are_validated_by_summary(tmp_path: Path):
    rows = pair(tmp_path / "constructed.jsonl")
    rows[-1] = replace(rows[-1], remaining_value=float("nan"), remaining_inference=None)
    shot = summarize(rows).shots[0]
    assert shot.attributed_usage is None and shot.observations[-1].remaining_value is None
    assert "INVALID_REMAINING_VALUE" in shot.reasons
