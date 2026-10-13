"""GPS track for the map, built from Wialon position messages.

Positions are split into driving segments at long silences, thinned with
Douglas-Peucker and returned as a GeoJSON MultiLineString small enough to
live in a state attribute. Parked GPS jitter is dropped.

Pure Python (no Home Assistant imports) so it can be tested standalone.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import itertools
import json
import math
from typing import Any

Point = tuple[float, float, float]  # (t, lat, lon)

GAP = 600  # s; a longer silence between positions starts a new segment
TOLERANCE = 5.0  # m; Douglas-Peucker tolerance
MIN_SEGMENT = 20.0  # m; shorter simplified segments are parked GPS jitter
MAX_BYTES = 65536  # compact-JSON size cap of the GeoJSON
TOLERANCE_STEPS = (5.0, 10.0, 20.0, 50.0, 100.0, 200.0)

EARTH_RADIUS = 6371000.0


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _dedupe(points: Iterable[Point]) -> list[Point]:
    """Sorted by t; for duplicate t the last one seen wins."""
    by_t: dict[float, Point] = {}
    for point in points:
        by_t[point[0]] = point
    return [by_t[t] for t in sorted(by_t)]


def extract_points(messages: Iterable[dict]) -> list[Point]:
    """(t, lat, lon) for every message that carries a usable GPS fix."""
    points: list[Point] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        t = msg.get("t")
        pos = msg.get("pos")
        if not _is_number(t) or not isinstance(pos, dict):
            continue
        lon, lat = pos.get("x"), pos.get("y")
        if not _is_number(lon) or not _is_number(lat):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        if lat == 0 and lon == 0:
            continue
        sc = pos.get("sc")
        if _is_number(sc) and sc == 0:
            continue  # no fix: Wialon repeats a stale position
        points.append((float(t), float(lat), float(lon)))
    return _dedupe(points)


def merge(points: list[Point], new: Iterable[Point]) -> list[Point]:
    """`points` plus the entries of `new` strictly newer than the last of them."""
    last = points[-1][0] if points else None
    fresh = [p for p in new if last is None or p[0] > last]
    return _dedupe([*points, *fresh])


def segments(points: list[Point], gap: float = GAP) -> list[list[Point]]:
    """Split at silences longer than `gap`; segments of one point are dropped."""
    result: list[list[Point]] = []
    current: list[Point] = []
    for point in points:
        if current and point[0] - current[-1][0] > gap:
            result.append(current)
            current = []
        current.append(point)
    result.append(current)
    return [s for s in result if len(s) >= 2]


def _haversine(a: Point, b: Point) -> float:
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = math.radians(b[2] - a[2])
    h = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * EARTH_RADIUS * math.asin(min(1.0, math.sqrt(h)))


def length_m(segment: list[Point]) -> float:
    """Haversine length of the polyline in metres."""
    return sum(_haversine(a, b) for a, b in itertools.pairwise(segment))


def _distance(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> float:
    """Distance from P to the line segment AB (point distance if A == B)."""
    dx, dy = bx - ax, by - ay
    sq = dx * dx + dy * dy
    if sq == 0:
        return math.hypot(px - ax, py - ay)
    u = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / sq))
    return math.hypot(px - (ax + u * dx), py - (ay + u * dy))


def simplify(segment: list[Point], tolerance: float) -> list[Point]:
    """Douglas-Peucker in metres (iterative); first and last always kept."""
    n = len(segment)
    if n <= 2:
        return list(segment)
    mean_lat = math.radians(sum(p[1] for p in segment) / n)
    k = math.cos(mean_lat) * EARTH_RADIUS
    xy = [(math.radians(p[2]) * k, math.radians(p[1]) * EARTH_RADIUS) for p in segment]

    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        first, last = stack.pop()
        if last - first < 2:
            continue
        ax, ay = xy[first]
        bx, by = xy[last]
        worst, index = -1.0, first
        for i in range(first + 1, last):
            d = _distance(xy[i][0], xy[i][1], ax, ay, bx, by)
            if d > worst:
                worst, index = d, i
        if worst > tolerance:
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))
    return [p for p, flag in zip(segment, keep, strict=True) if flag]


@dataclass(frozen=True)
class Track:
    """A drawable track; `geojson` is None when there is nothing to draw."""

    geojson: dict | None
    distance_km: float
    points: int  # input positions
    kept: int  # positions in the GeoJSON
    tolerance: float  # tolerance actually used for the GeoJSON


def _simplified(
    segs: list[list[Point]], tolerance: float, min_segment: float
) -> list[list[Point]]:
    out = (simplify(s, tolerance) for s in segs)
    return [s for s in out if length_m(s) >= min_segment]


def _geojson(segs: list[list[Point]]) -> dict | None:
    if not segs:
        return None
    return {
        "type": "MultiLineString",
        "coordinates": [[[round(p[2], 6), round(p[1], 6)] for p in s] for s in segs],
    }


def _size(geojson: dict | None) -> int:
    return len(json.dumps(geojson, separators=(",", ":")))


def build_track(
    points: list[Point],
    *,
    gap: float = GAP,
    tolerance: float = TOLERANCE,
    min_segment: float = MIN_SEGMENT,
    max_bytes: int = MAX_BYTES,
) -> Track:
    """Simplified GeoJSON and distance for `points`, within `max_bytes`."""
    if not points:
        return Track(None, 0.0, 0, 0, tolerance)

    segs = segments(points, gap)
    base = _simplified(segs, tolerance, min_segment)
    distance_km = round(sum(length_m(s) for s in base) / 1000, 2)

    used = tolerance
    chosen = base
    geojson = _geojson(chosen)
    if geojson is not None and _size(geojson) > max_bytes:
        for step in TOLERANCE_STEPS:
            if step <= tolerance:
                continue
            used = step
            chosen = _simplified(segs, step, min_segment)
            geojson = _geojson(chosen)
            if geojson is None or _size(geojson) <= max_bytes:
                break
        else:
            # Still too big at the largest step: the oldest segments go first.
            while len(chosen) > 1 and _size(geojson) > max_bytes:
                chosen = chosen[1:]
                geojson = _geojson(chosen)

    kept = sum(len(s) for s in chosen)
    return Track(geojson, distance_km, len(points), kept, used)
