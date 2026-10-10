#!/usr/bin/env python3
"""Generate custom_components/wialon/xirgo_sensors.py from Xirgo's public docs.

Stdlib only. Usage: python scripts/gen_xirgo_sensors.py
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
import re
import urllib.request

SENSORS_URL = "https://docs.xirgoglobal.com/space/SD/27149650.md"
LEGACY_URL = "https://docs.xirgoglobal.com/space/SD/27138419.md"
OUTPUT = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "wialon"
    / "xirgo_sensors.py"
)

_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")


def fetch(url: str) -> str:
    """Download a page as text."""
    request = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(request, timeout=30) as resp:  # noqa: S310
        return resp.read().decode("utf-8")


def _cell(value: str) -> str | None:
    value = value.strip()
    return None if value in ("", "-") else value


def _number(value: str | None) -> float | None:
    if value is None or not _NUMBER_RE.match(value):
        return None
    return float(value)


def parse_rows(markdown: str) -> dict[int, list[str | None]]:
    """Parse markdown table rows into {id: [cells after the ID]}.

    Skips headers, separators, empty rows, bold section rows and rows whose
    Name is "-". A "-" or empty cell becomes None.
    """
    rows: dict[int, list[str | None]] = {}
    for line in markdown.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or not re.fullmatch(r"\d+", cells[0]):
            continue
        parsed = [_cell(c) for c in cells[1:]]
        if not parsed or parsed[0] is None:
            continue
        rows[int(cells[0])] = parsed
    return rows


def build_table(
    sensors_md: str, legacy_md: str
) -> dict[int, tuple[str, str | None, float | None, float | None, str | None]]:
    """Merge both pages into {id: (name, alt_name, multiplier, offset, unit)}."""
    legacy = parse_rows(legacy_md)
    table = {}
    for sensor_id, cells in parse_rows(sensors_md).items():
        # cells: name, reset value, multiplier, offset, unit, period, set_val
        cells = cells + [None] * (7 - len(cells))
        alt = legacy.get(sensor_id, [None, None])[1:2]
        table[sensor_id] = (
            cells[0],
            alt[0] if alt else None,
            _number(cells[2]),
            _number(cells[3]),
            cells[4],
        )
    return table


def render(table: dict, today: date) -> str:
    """Render the Python module source."""
    lines = [
        '"""Xirgo sensor table (ID -> name, multiplier, offset, unit).',
        "",
        f"Sources: {SENSORS_URL}",
        f"         {LEGACY_URL}",
        f"Generated {today.isoformat()}.",
        "",
        "Generated - do not edit; regenerate with scripts/gen_xirgo_sensors.py",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import NamedTuple",
        "",
        "",
        "class XirgoSensor(NamedTuple):",
        '    """One row of the Xirgo sensor table."""',
        "",
        "    name: str",
        "    alt_name: str | None",
        "    multiplier: float | None",
        "    offset: float | None",
        "    unit: str | None",
        "",
        "",
        "XIRGO_SENSORS: dict[int, XirgoSensor] = {",
    ]
    for sensor_id in sorted(table):
        name, alt, mult, off, unit = table[sensor_id]
        lines.append(
            f"    {sensor_id}: XirgoSensor({name!r}, {alt!r}, {mult!r}, {off!r}, {unit!r}),"
        )
    lines += ["}", ""]
    return "\n".join(lines)


def main() -> None:
    table = build_table(fetch(SENSORS_URL), fetch(LEGACY_URL))
    OUTPUT.write_text(render(table, date.today()), encoding="utf-8", newline="\n")
    print(f"Wrote {len(table)} sensors to {OUTPUT}")


if __name__ == "__main__":
    main()
