"""Tests for the GPS track builder (pure, no Home Assistant)."""

from __future__ import annotations

import itertools
import json
import math

import pytest

LAT0, LON0 = 10.0, 20.0
M_PER_DEG_LAT = 111194.9  # 6371000 m * pi / 180


def msg(t, lat=LAT0, lon=LON0, **pos):
    return {"t": t, "pos": {"x": lon, "y": lat, **pos}}


def line(t0, n, dlat=0.0002, dlon=0.0, lat=LAT0, lon=LON0, dt=5):
    return [(t0 + i * dt, lat + i * dlat, lon + i * dlon) for i in range(n)]


def size(geojson) -> int:
    return len(json.dumps(geojson, separators=(",", ":")))


# --- extract_points ---------------------------------------------------------


def test_extract_filters(track):
    messages = [
        msg(1, sc=0),  # no fix
        msg(2),  # sc missing: kept
        msg(3, sc=7),
        msg(4, lat=0.0, lon=0.0),
        msg(5, lat=91.0),
        msg(6, lon=181.0),
        msg(7, lat=-90.5),
        {"t": True, "pos": {"x": 20.0, "y": 10.0}},
        {"t": 8, "pos": "nope"},
        {"t": 9},
        {"t": 10, "pos": {"x": "20", "y": 10.0}},
        {"t": 11, "pos": {"x": 20.0}},
        {"t": "12", "pos": {"x": 20.0, "y": 10.0}},
        "garbage",
        None,
    ]
    points = track.extract_points(messages)
    assert [p[0] for p in points] == [2, 3]
    assert points[0] == (2.0, LAT0, LON0)


def test_extract_zero_on_one_axis_is_kept(track):
    assert len(track.extract_points([msg(1, lat=0.0, lon=20.0)])) == 1


def test_extract_sorted_and_last_duplicate_wins(track):
    points = track.extract_points(
        [msg(30, lat=3.0), msg(10, lat=1.0), msg(20, lat=2.0), msg(10, lat=1.5)]
    )
    assert [p[0] for p in points] == [10, 20, 30]
    assert points[0][1] == 1.5


# --- merge ------------------------------------------------------------------


def test_merge_appends_only_newer(track):
    a = [(1.0, 1.0, 1.0), (2.0, 1.0, 1.0)]
    new = [(1.0, 9.0, 9.0), (2.0, 9.0, 9.0), (3.0, 2.0, 2.0), (3.0, 3.0, 3.0)]
    merged = track.merge(a, new)
    assert merged == [(1.0, 1.0, 1.0), (2.0, 1.0, 1.0), (3.0, 3.0, 3.0)]
    assert len(a) == 2  # input untouched


def test_merge_into_empty(track):
    assert track.merge([], [(2.0, 1.0, 1.0), (1.0, 1.0, 1.0)]) == [
        (1.0, 1.0, 1.0),
        (2.0, 1.0, 1.0),
    ]


# --- segments ---------------------------------------------------------------


def test_segments_split_at_gap(track):
    pts = [*line(0, 3), *line(1000, 3), *line(5000, 1)]
    segs = track.segments(pts)
    assert len(segs) == 2  # the lone trailing point is dropped
    for seg in segs:
        for a, b in itertools.pairwise(seg):
            assert b[0] - a[0] <= track.GAP


def test_segments_gap_boundary_stays_joined(track):
    pts = [(0.0, LAT0, LON0), (600.0, LAT0, LON0), (1201.0, LAT0, LON0)]
    assert [len(s) for s in track.segments(pts)] == [2]


def test_segments_empty_and_single(track):
    assert track.segments([]) == []
    assert track.segments([(0.0, LAT0, LON0)]) == []


# --- simplify ---------------------------------------------------------------


def test_simplify_collinear(track):
    seg = line(0, 50)
    assert track.simplify(seg, 5.0) == [seg[0], seg[-1]]


def test_simplify_keeps_corner(track):
    leg = line(0, 10, dlat=0.0005)
    corner = leg[-1]
    other = [
        (corner[0] + 5 * i, corner[1], corner[2] + 0.0005 * i) for i in range(1, 10)
    ]
    seg = leg + other
    out = track.simplify(seg, 5.0)
    assert out == [seg[0], corner, seg[-1]]


def test_simplify_removes_wiggle_within_tolerance(track):
    # ~1 m sideways wiggle on a straight line
    wiggle = 1.0 / (M_PER_DEG_LAT * math.cos(math.radians(LAT0)))
    seg = [
        (i * 5.0, LAT0 + i * 0.0002, LON0 + (wiggle if i % 2 else 0.0))
        for i in range(20)
    ]
    assert track.simplify(seg, 5.0) == [seg[0], seg[-1]]
    assert len(track.simplify(seg, 0.1)) > 2


def test_simplify_short_segments_unchanged(track):
    assert track.simplify([], 5.0) == []
    one = [(0.0, LAT0, LON0)]
    assert track.simplify(one, 5.0) == one
    two = line(0, 2)
    assert track.simplify(two, 5.0) == two


def test_simplify_closed_loop(track):
    # first == last: out and back; the far point must survive
    seg = [(0.0, LAT0, LON0), (5.0, LAT0 + 0.001, LON0), (10.0, LAT0, LON0)]
    assert track.simplify(seg, 5.0) == seg


def test_simplify_large_segment_no_recursion_error(track):
    # zigzag with a 20 m amplitude: nearly every point survives at 5 m
    amp = 20.0 / (M_PER_DEG_LAT * math.cos(math.radians(LAT0)))
    seg = [
        (float(i), LAT0 + i * 1e-5, LON0 + (amp if i % 2 else 0.0))
        for i in range(10_000)
    ]
    out = track.simplify(seg, 5.0)
    assert len(out) > 9_900
    assert out[0] == seg[0]
    assert out[-1] == seg[-1]
    # and a straight 10 000 point line collapses
    straight = [(float(i), LAT0 + i * 1e-5, LON0) for i in range(10_000)]
    assert len(track.simplify(straight, 5.0)) == 2


# --- length_m ---------------------------------------------------------------


def test_length_known_distance(track):
    seg = [(0.0, LAT0, LON0), (1.0, LAT0 + 0.001, LON0)]
    assert track.length_m(seg) == pytest.approx(111.2, abs=0.5)


def test_length_sums_and_degenerate(track):
    assert track.length_m([]) == 0.0
    assert track.length_m([(0.0, LAT0, LON0)]) == 0.0
    seg = line(0, 3, dlat=0.001)
    assert track.length_m(seg) == pytest.approx(222.4, abs=1.0)


# --- build_track ------------------------------------------------------------


def test_build_empty(track):
    result = track.build_track([])
    assert result == track.Track(None, 0.0, 0, 0, track.TOLERANCE)


def test_build_geojson_shape_order_and_rounding(track):
    pts = [(0.0, 10.12345678, 20.98765432), (5.0, 10.12445678, 20.98765432)]
    result = track.build_track(pts)
    assert result.geojson == {
        "type": "MultiLineString",
        "coordinates": [[[20.987654, 10.123457], [20.987654, 10.124457]]],
    }
    assert result.points == 2
    assert result.kept == 2
    assert result.tolerance == track.TOLERANCE
    assert result.distance_km == pytest.approx(0.11, abs=0.005)


def test_build_collinear_reduced(track):
    result = track.build_track(line(0, 100))
    assert result.points == 100
    assert result.kept == 2
    assert len(result.geojson["coordinates"]) == 1


def test_build_drops_parked_jitter(track):
    jitter = [(float(i), LAT0 + (i % 2) * 0.00002, LON0) for i in range(30)]  # ~2 m
    drive = line(1000, 20)
    result = track.build_track([*jitter, *drive])
    assert len(result.geojson["coordinates"]) == 1
    assert result.geojson["coordinates"][0][0][1] == pytest.approx(LAT0)
    assert track.build_track(jitter).geojson is None
    assert track.build_track(jitter).distance_km == 0.0


def _zigzag(track, t0, n, amp_m=300.0):
    amp = amp_m / (M_PER_DEG_LAT * math.cos(math.radians(LAT0)))
    return [
        (t0 + i * 5.0, LAT0 + i * 0.001, LON0 + (amp if i % 2 else 0.0))
        for i in range(n)
    ]


def test_build_size_cap_raises_tolerance_distance_unchanged(track):
    pts = _zigzag(track, 0, 200, amp_m=30.0)
    base = track.build_track(pts)
    assert base.tolerance == track.TOLERANCE
    assert base.kept == 200
    cap = size(base.geojson) // 2
    capped = track.build_track(pts, max_bytes=cap)
    assert capped.tolerance > track.TOLERANCE
    assert capped.kept < base.kept
    assert size(capped.geojson) <= cap
    assert capped.distance_km == base.distance_km
    assert capped.points == 200


def test_build_drops_oldest_segments_when_nothing_fits(track):
    pts = []
    for k in range(6):
        pts += _zigzag(track, k * 10_000, 6, amp_m=1000.0)
        # shift each segment so it is identifiable by latitude
        pts = [(t, lat + (0.1 if t >= k * 10_000 else 0.0), lon) for t, lat, lon in pts]
    full = track.build_track(pts)
    assert len(full.geojson["coordinates"]) == 6
    cap = size(full.geojson) // 2
    result = track.build_track(pts, max_bytes=cap)
    assert result.tolerance == track.TOLERANCE_STEPS[-1]
    assert size(result.geojson) <= cap
    kept = result.geojson["coordinates"]
    assert 1 <= len(kept) < 6
    # the newest segment survives
    assert kept[-1] == full.geojson["coordinates"][-1]
    assert result.kept == sum(len(s) for s in kept)
    assert result.distance_km == full.distance_km


def test_build_single_oversized_segment_kept(track):
    pts = _zigzag(track, 0, 10, amp_m=1000.0)
    result = track.build_track(pts, max_bytes=10)
    assert result.geojson is not None
    assert len(result.geojson["coordinates"]) == 1
    assert result.kept > 0


def test_build_gap_makes_multiple_lines(track):
    pts = [*line(0, 10, dlat=0.0005), *line(5000, 10, dlat=0.0005, lon=LON0 + 0.01)]
    result = track.build_track(pts)
    assert len(result.geojson["coordinates"]) == 2
