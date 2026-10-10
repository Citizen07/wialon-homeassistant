"""Tests for the hourly aggregation (pure, no Home Assistant)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

H = 3600
BASE = 1_700_000_000 // H * H  # an hour boundary


def at(hour: int, minute: int = 0) -> float:
    return BASE + hour * H + minute * 60


def ts(row) -> int:
    return int(row["start"].timestamp())


def test_weighted_mean_uneven_samples(history):
    # 10 for 15 min, 20 for 45 min -> 17.5
    samples = [(at(0), 10.0), (at(0, 15), 20.0)]
    rows = history.hourly_measurement(samples, end_ts=at(1))
    assert len(rows) == 1
    assert rows[0]["mean"] == pytest.approx(17.5)
    assert (rows[0]["min"], rows[0]["max"]) == (10.0, 20.0)
    assert rows[0]["start"] == datetime.fromtimestamp(at(0), UTC)


def test_carry_in_across_hour_boundary(history):
    samples = [(at(0, 30), 10.0), (at(1, 30), 30.0)]
    rows = history.hourly_measurement(samples, end_ts=at(2))
    assert [ts(r) for r in rows] == [at(0), at(1)]
    assert rows[0]["mean"] == pytest.approx(10.0)  # only the covered half hour
    # hour 1: 10 for 30 min, 30 for 30 min
    assert rows[1]["mean"] == pytest.approx(20.0)
    assert (rows[1]["min"], rows[1]["max"]) == (10.0, 30.0)


def test_min_max_include_carried_value(history):
    samples = [(at(0, 10), 5.0), (at(1, 50), 8.0)]
    rows = history.hourly_measurement(samples, end_ts=at(2))
    # hour 1 starts holding 5.0; its only sample is 8.0
    assert rows[1]["min"] == 5.0 and rows[1]["max"] == 8.0


def test_no_rows_before_first_sample(history):
    rows = history.hourly_measurement([(at(3, 1), 1.0)], end_ts=at(4))
    assert [ts(r) for r in rows] == [at(3)]


def test_carry_cap_24h(history):
    samples = [(at(0), 1.0), (at(48), 2.0)]
    rows = history.hourly_measurement(samples, end_ts=at(49))
    starts = [ts(r) for r in rows]
    assert at(0) in starts and at(23) in starts
    assert at(24) not in starts and at(47) not in starts
    assert starts[-1] == at(48)


def test_last_sample_without_end_covers_only_its_hour(history):
    rows = history.hourly_measurement([(at(0, 20), 4.0)])
    assert len(rows) == 1 and rows[0]["mean"] == 4.0


def test_empty_inputs(history):
    assert history.hourly_measurement([]) == []
    assert history.hourly_total([]) == []
    assert history.chain_sums([], (1.0, 2.0)) == []


def test_total_state_and_carry(history):
    samples = [(at(0, 10), 100.0), (at(0, 50), 101.0), (at(3, 5), 105.0)]
    rows = history.hourly_total(samples)
    assert [(ts(r), r["state"]) for r in rows] == [
        (at(0), 101.0),
        (at(1), 101.0),
        (at(2), 101.0),
        (at(3), 105.0),
    ]


def test_total_carry_cap(history):
    rows = history.hourly_total([(at(0), 1.0), (at(40), 2.0)])
    starts = [ts(r) for r in rows]
    assert at(23) in starts and at(24) not in starts and at(39) not in starts
    assert starts[-1] == at(40)


def test_total_rejects_backwards_glitch(history):
    samples = [(at(0), 1000.0), (at(1), 500.0), (at(2), 1005.0), (at(3), 996.0)]
    rows = history.hourly_total(samples)
    # 500 is a glitch; 996 is within 1 % of 1005 and kept
    assert [r["state"] for r in rows] == [1000.0, 1000.0, 1005.0, 996.0]


def test_chain_sums_with_anchor(history):
    rows = [{"start": 1, "state": 90.0}, {"start": 2, "state": 100.0}]
    out = history.chain_sums(rows, (500.0, 110.0))
    assert [r["sum"] for r in out] == [480.0, 490.0]  # negatives relative to V0 ok
    assert "sum" not in rows[0]


def test_chain_sums_without_anchor(history):
    rows = [{"start": 1, "state": 90.0}, {"start": 2, "state": 100.0}]
    assert [r["sum"] for r in history.chain_sums(rows, None)] == [0.0, 10.0]


def test_rows_before_cutoff(history):
    rows = history.hourly_total([(at(0), 1.0), (at(1), 2.0), (at(2), 3.0)])
    cut = datetime.fromtimestamp(at(2), UTC)
    assert [ts(r) for r in history.rows_before(rows, cut)] == [at(0), at(1)]
    assert len(history.rows_before(rows, None)) == 3


def test_aggregate_series(history):
    msgs = [
        {"t": at(0, 5), "p": {"a": 1, "b": 10}},
        {"t": at(0, 35), "p": {"a": 3}},
        {"t": at(1, 5), "p": {"a": "x", "b": 12}},
    ]
    series = [
        history.Series("sensor.a", lambda m: m["p"].get("a"), "measurement"),
        history.Series("sensor.b", lambda m: m["p"].get("b"), "total"),
    ]
    out = history.aggregate(msgs, series, end_ts=at(2))
    assert [round(r["mean"], 3) for r in out["sensor.a"][:1]] == [
        round((1 * 30 + 3 * 25) / 55, 3)
    ]
    assert [r["state"] for r in out["sensor.b"]] == [10.0, 12.0]


def test_compact_is_lossless_for_hourly_aggregates(history):
    H = 3600
    raw = [(t * 60.0, 5.0) for t in range(0, 180)]  # 3 h of a parked value
    raw += [(3 * H + t * 60.0, float(t)) for t in range(0, 60)]  # 1 h driving
    kept = history.compact(raw)
    assert len(kept) < len(raw) / 3
    end = 4 * H
    assert history.hourly_measurement(kept, end) == history.hourly_measurement(raw, end)
    assert history.hourly_total(kept, end) == history.hourly_total(raw, end)


def test_compact_keeps_one_per_interval_and_continues_chunks(history):
    H = 3600
    first = history.compact([(0.0, 1.0), (1800.0, 1.0)])
    assert first == [(0.0, 1.0)]
    second = history.compact([(H + 1.0, 1.0), (H + 60.0, 2.0)], first[-1])
    assert second == [(H + 1.0, 1.0), (H + 60.0, 2.0)]


def test_no_reading_ends_hold_and_adds_nothing(history):
    H = 3600
    nan = history.NO_READING
    # 90 °C at shutdown, then the CAN bus sleeps (raw 0 -> no reading) for 3 h.
    samples = [(0.0, 90.0), (600.0, nan), (1200.0, nan), (3 * H + 600.0, 20.0)]
    rows = history.hourly_measurement(samples, 4 * H)
    by_hour = {int(r["start"].timestamp()) // H: r for r in rows}
    assert by_hour[0]["mean"] == 90.0  # held only until the first no-reading
    assert 1 not in by_hour and 2 not in by_hour
    assert by_hour[3]["min"] == 20.0 and by_hour[3]["max"] == 20.0


def test_compact_collapses_no_reading_runs(history):
    nan = history.NO_READING
    kept = history.compact([(0.0, nan), (60.0, nan), (120.0, 5.0)])
    assert len(kept) == 2


def test_total_ignores_no_reading(history):
    nan = history.NO_READING
    rows = history.hourly_total([(0.0, 10.0), (60.0, nan)], 120.0)
    assert [r["state"] for r in rows] == [10.0]
