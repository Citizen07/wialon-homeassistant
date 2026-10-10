"""Hourly aggregation of Wialon messages for long-term statistics.

Pure Python (no Home Assistant imports) so it can be tested standalone.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
import math
from typing import Any

HOUR = 3600
MAX_CARRY = 24 * HOUR
GLITCH_FRACTION = 0.01

Sample = tuple[float, float]
# A message whose value is "no reading" (e.g. coolant raw 0 while the CAN bus
# sleeps). It ends the previous sample's hold, like HA's own "unknown" state,
# and contributes nothing itself.
NO_READING = float("nan")
Row = dict[str, Any]


@dataclass(frozen=True)
class Series:
    """One statistic to build from messages."""

    statistic_id: str
    extractor: Callable[[dict], float | None]
    kind: str  # "measurement" or "total"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def extract_samples(
    messages: Iterable[dict], extractor: Callable[[dict], float | None]
) -> list[Sample]:
    """(time, value) pairs from messages; messages without a value are skipped."""
    samples: list[Sample] = []
    for msg in messages:
        t = msg.get("t")
        if not _is_number(t):
            continue
        value = extractor(msg)
        if _is_number(value):
            samples.append((float(t), float(value)))
    samples.sort(key=lambda s: s[0])
    return samples


def _same(a: float, b: float) -> bool:
    return a == b or (math.isnan(a) and math.isnan(b))


def compact(
    samples: list[Sample], last: Sample | None = None, keep_every: float = HOUR
) -> list[Sample]:
    """Drop samples that repeat the last kept value within keep_every seconds.

    A repeated value changes nothing the hourly aggregates compute (a sample
    holds until the next one), so this is lossless for them; keeping one per
    keep_every stops a long run of identical values from looking like a gap
    to the MAX_CARRY cap. `last` is the last sample kept from an earlier chunk.
    It keeps a year of minute-rate messages to a few samples per parked hour.
    """
    kept: list[Sample] = []
    for sample in samples:
        if (
            last is not None
            and _same(sample[1], last[1])
            and sample[0] - last[0] < keep_every
        ):
            continue
        kept.append(sample)
        last = sample
    return kept


def _hour_start(ts: float) -> int:
    return int(ts // HOUR) * HOUR


def _dt(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, UTC)


def hourly_measurement(
    samples: list[Sample], end_ts: float | None = None
) -> list[Row]:
    """Time-weighted mean, min and max per hour.

    A sample holds until the next one, for at most MAX_CARRY. The last sample
    holds until min(its time + MAX_CARRY, end_ts); without end_ts it covers no
    time. Hours before the first sample get no row.
    """
    acc: dict[int, list[float]] = {}  # hour -> [dur, weighted, min, max, n, sum]
    for i, (t0, v) in enumerate(samples):
        if math.isnan(v):
            continue  # ended the previous hold; holds nothing itself
        limit = t0 + MAX_CARRY
        if i + 1 < len(samples):
            t1 = min(samples[i + 1][0], limit)
        else:
            t1 = min(limit, end_ts) if end_ts is not None else t0
        t1 = max(t1, t0)
        first = _hour_start(t0)
        slot = acc.setdefault(first, [0.0, 0.0, v, v, 0, 0.0])
        slot[2], slot[3] = min(slot[2], v), max(slot[3], v)
        slot[4] += 1
        slot[5] += v
        h = first
        while h < t1:
            lo, hi = max(t0, h), min(t1, h + HOUR)
            if hi > lo:
                slot = acc.setdefault(h, [0.0, 0.0, v, v, 0, 0.0])
                slot[0] += hi - lo
                slot[1] += (hi - lo) * v
                slot[2], slot[3] = min(slot[2], v), max(slot[3], v)
            h += HOUR
    rows: list[Row] = []
    for h in sorted(acc):
        dur, weighted, lo, hi, count, total = acc[h]
        mean = weighted / dur if dur > 0 else total / count
        rows.append({"start": _dt(h), "mean": mean, "min": lo, "max": hi})
    return rows


def hourly_total(samples: list[Sample], end_ts: float | None = None) -> list[Row]:
    """Last value per hour for a rising counter, carried over gaps.

    A value lower than the last accepted one by more than 1 % is a glitch and
    is ignored. Carry-forward stops MAX_CARRY after the last accepted sample;
    without end_ts nothing is carried past the last sample's hour.
    """
    accepted: list[Sample] = []
    last: float | None = None
    for t, v in samples:
        if math.isnan(v):
            continue
        if last is not None and v < last - abs(last) * GLITCH_FRACTION:
            continue
        accepted.append((t, v))
        last = v
    rows: list[Row] = []
    if not accepted:
        return rows
    idx = 0
    h = _hour_start(accepted[0][0])
    final_hour = _hour_start(accepted[-1][0])
    if end_ts is not None:
        final_hour = max(
            final_hour, _hour_start(min(accepted[-1][0] + MAX_CARRY, end_ts))
        )
    state: Sample | None = None
    while h <= final_hour:
        while idx < len(accepted) and accepted[idx][0] < h + HOUR:
            state = accepted[idx]
            idx += 1
        if state is not None:
            in_hour = state[0] >= h
            if in_hour or (state[0] + MAX_CARRY > h and (end_ts is None or h < end_ts)):
                rows.append({"start": _dt(h), "state": state[1]})
        h += HOUR
    return rows


def aggregate(
    messages: list[dict], series: Iterable[Series], end_ts: float | None = None
) -> dict[str, list[Row]]:
    """Hourly rows per statistic_id (messages must be sorted by time)."""
    out: dict[str, list[Row]] = {}
    for s in series:
        samples = extract_samples(messages, s.extractor)
        if s.kind == "total":
            out[s.statistic_id] = hourly_total(samples, end_ts)
        else:
            out[s.statistic_id] = hourly_measurement(samples, end_ts)
    return out


def rows_before(rows: list[Row], cutoff: datetime | None) -> list[Row]:
    """Rows whose hour starts strictly before cutoff (all rows when None)."""
    if cutoff is None:
        return list(rows)
    return [r for r in rows if r["start"] < cutoff]


def chain_sums(rows: list[Row], anchor: tuple[float, float] | None) -> list[Row]:
    """Add `sum` to total rows.

    anchor is (sum, state) of the earliest existing statistic row:
    sum_h = S0 + (state_h - V0). Without one, sums start at 0 for the first row.
    """
    if not rows:
        return []
    if anchor is None:
        s0, v0 = 0.0, rows[0]["state"]
    else:
        s0, v0 = anchor
    return [{**r, "sum": s0 + (r["state"] - v0)} for r in rows]
