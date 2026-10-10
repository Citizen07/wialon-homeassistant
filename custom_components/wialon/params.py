"""Conversion of raw Wialon device parameters via the Xirgo sensor table.

Pure Python (no Home Assistant imports) so it can be tested standalone.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import math
import re
from typing import Any

from .xirgo_sensors import XIRGO_SENSORS

ACRONYMS = {
    "GNSS",
    "GSM",
    "GPRS",
    "PDP",
    "CAN",
    "ID",
    "ADC",
    "DTC",
    "MIL",
    "MAF",
    "MAP",
    "DPF",
    "EPC",
    "HDOP",
    "RPM",
}

_WORD_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


@dataclass(frozen=True)
class ParamSpec:
    """Mapping of a Wialon parameter key to a Xirgo sensor, with overrides."""

    sensor_id: int
    multiplier: float | None = None
    offset: float | None = None
    unit: str | None = None
    name: str | None = None
    precision: int | None = None
    device_class: str | None = None
    state_class: str = "measurement"
    source: str = "xirgo"


_TOTAL = "total_increasing"
_J1979 = "sae_j1979"

PARAM_MAP: dict[str, ParamSpec] = {
    # J1939 (Xirgo table)
    "can_taho": ParamSpec(12300, precision=0),
    "engine_temp": ParamSpec(8200, device_class="temperature"),
    "j1939_air_temp": ParamSpec(12330, device_class="temperature"),
    "j1939_fuel_rate": ParamSpec(12321, device_class="volume_flow_rate"),
    "j1939_flfmseco": ParamSpec(12340),
    "total_fuel": ParamSpec(16385, device_class="volume", state_class=_TOTAL),
    "j1939_fl_con": ParamSpec(
        16474,
        name="Total fuel used (high resolution)",
        device_class="volume",
        state_class=_TOTAL,
    ),
    "can_engine_hrs": ParamSpec(16386, device_class="duration", state_class=_TOTAL),
    "can_mileage": ParamSpec(
        16387, precision=1, device_class="distance", state_class=_TOTAL
    ),
    "j1939_adblue": ParamSpec(8225),
    "acc_pedal": ParamSpec(8198),
    "j1939_eng_load": ParamSpec(8202),
    "axle1": ParamSpec(12333, device_class="weight"),
    "axle2": ParamSpec(12334, device_class="weight"),
    "axle3": ParamSpec(12335, device_class="weight"),
    "axle4": ParamSpec(12336, device_class="weight"),
    "axle5": ParamSpec(12337, device_class="weight"),
    "srv_dist": ParamSpec(12344, device_class="distance"),
    # J1979 / OBD-II: user-defined slots, SAE J1979 conversion
    "j1979_speed": ParamSpec(
        8219, multiplier=1, offset=0, unit="km/h", device_class="speed", source=_J1979
    ),
    "j1979_eng_temp": ParamSpec(
        8212,
        multiplier=1,
        offset=-40,
        unit="°C",
        device_class="temperature",
        source=_J1979,
    ),
    "j1979_air_temp": ParamSpec(
        8220,
        multiplier=1,
        offset=-40,
        unit="°C",
        device_class="temperature",
        source=_J1979,
    ),
    "j1979_eng_load": ParamSpec(
        8211, multiplier=100 / 255, offset=0, unit="%", source=_J1979
    ),
    "j1979_tps": ParamSpec(
        8221,
        multiplier=100 / 255,
        offset=0,
        unit="%",
        name="J1979 throttle position",
        source=_J1979,
    ),
    "j1979_fl_lvl": ParamSpec(
        8222, multiplier=100 / 255, offset=0, unit="%", source=_J1979
    ),
    "j1979_fl_pres": ParamSpec(
        8217,
        multiplier=3,
        offset=0,
        unit="kPa",
        name="J1979 fuel pressure",
        device_class="pressure",
        source=_J1979,
    ),
    "j1979_rpm": ParamSpec(12347, precision=0),
    "j1979_flrate": ParamSpec(12349, device_class="volume_flow_rate"),
    "j1979_maf": ParamSpec(12348),
}


@dataclass(frozen=True)
class ResolvedParam:
    """Effective conversion and presentation of a parameter."""

    name: str
    multiplier: float
    offset: float
    unit: str | None
    device_class: str | None
    state_class: str
    precision: int


def _split_token(token: str) -> list[str]:
    if token.isdigit() or token.isupper() or token.islower():
        return [token]
    return _WORD_RE.findall(token)


def _derive_name(sensor_name: str, alt_name: str | None) -> str:
    raw = sensor_name
    if (
        "_USER_DEFINED_" in sensor_name
        and alt_name
        and not alt_name.lower().startswith("sensor_reserved")
    ):
        raw = alt_name
    raw = re.sub(r"^sensor_", "", raw, flags=re.IGNORECASE)
    words: list[str] = []
    for token in raw.split("_"):
        if token:
            words.extend(_split_token(token))
    out: list[str] = []
    for word in words:
        upper = word.upper()
        if upper == "ADBLUE":
            out.append("AdBlue")
        elif upper in ("1979", "J1979"):
            out.append("J1979")
        elif upper in ACRONYMS:
            out.append(upper)
        else:
            out.append(word.lower())
    if out and out[0] == out[0].lower():
        out[0] = out[0][:1].upper() + out[0][1:]
    return " ".join(out)


def _decimals(value: float) -> int:
    exponent = Decimal(repr(float(value))).normalize().as_tuple().exponent
    return max(-exponent, 0)


def _precision(multiplier: float, offset: float) -> int:
    if float(multiplier).is_integer() and float(offset).is_integer():
        return 0
    exact = max(_decimals(multiplier), _decimals(offset))
    if exact <= 3:
        return exact
    # Long fractions: show down to the first significant digit of the step.
    return max(1, min(3, math.ceil(-math.log10(abs(multiplier)))))


def resolve(key: str) -> ResolvedParam | None:
    """Resolve a Wialon parameter key, or None when it is not mapped."""
    spec = PARAM_MAP.get(key)
    if spec is None:
        return None
    row = XIRGO_SENSORS.get(spec.sensor_id)
    if row is None:
        return None

    def pick(override: Any, value: Any, default: Any) -> Any:
        if override is not None:
            return override
        return default if value is None else value

    multiplier = float(pick(spec.multiplier, row.multiplier, 1.0))
    offset = float(pick(spec.offset, row.offset, 0.0))
    return ResolvedParam(
        name=spec.name or _derive_name(row.name, row.alt_name),
        multiplier=multiplier,
        offset=offset,
        unit=pick(spec.unit, row.unit, None),
        device_class=spec.device_class,
        state_class=spec.state_class,
        precision=(
            spec.precision
            if spec.precision is not None
            else _precision(multiplier, offset)
        ),
    )


def convert(resolved: ResolvedParam, raw: Any) -> float | None:
    """Apply multiplier and offset to a raw value; None when not numeric."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return raw * resolved.multiplier + resolved.offset
