"""Parameter sensor tests; need Home Assistant (CI installs it via PHCC)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from custom_components.wialon.api import MessageWindow  # noqa: E402
from custom_components.wialon.sensor import WialonParamSensor  # noqa: E402

UNIT_ID = 1
WINDOW_TO = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _coordinator(params: dict[str, list]) -> SimpleNamespace:
    """Just what the entity reads: data, windows and the entry's data."""
    window = MessageWindow(
        time_from=datetime(2026, 1, 1, 11, 0, tzinfo=UTC),
        time_to=WINDOW_TO,
        count=3,
        params=params,
    )
    return SimpleNamespace(
        data={UNIT_ID: {"id": UNIT_ID}},
        windows={UNIT_ID: window},
        config_entry=SimpleNamespace(data={}),
        last_update_success=True,
    )


def test_unmapped_numeric_param_window_attributes() -> None:
    """A raw numeric key has no display precision yet still gets extremes."""
    key = "test_raw_param"
    coordinator = _coordinator({key: [3, 7.5, "x", 1]})
    sensor = WialonParamSensor(coordinator, UNIT_ID, {"nm": "Unit"}, key, 5)

    attrs = sensor.extra_state_attributes

    assert attrs["window_min"] == 1
    assert attrs["window_max"] == 7.5
    assert attrs["window_to"] == WINDOW_TO.isoformat()


def test_mapped_numeric_param_window_attributes_rounded() -> None:
    """A mapped key converts the extremes and rounds them to its precision."""
    key = "j1939_fuel_rate"  # 0.05 L/h per bit, 2 decimals
    coordinator = _coordinator({key: [3, 7, 1]})
    sensor = WialonParamSensor(coordinator, UNIT_ID, {"nm": "Unit"}, key, 5)

    attrs = sensor.extra_state_attributes

    assert attrs["window_min"] == 0.05
    assert attrs["window_max"] == 0.35


def test_unmapped_text_param_has_no_window_attributes() -> None:
    """A non-numeric sample skips the window entirely."""
    key = "test_raw_text"
    coordinator = _coordinator({key: ["a", "b"]})
    sensor = WialonParamSensor(coordinator, UNIT_ID, {"nm": "Unit"}, key, "a")

    attrs = sensor.extra_state_attributes

    assert "window_min" not in attrs
    assert "window_to" not in attrs


def test_counter_zero_holds_last_reading() -> None:
    """A 0 odometer keeps the last real value, so statistics see no reset."""
    key = "can_mileage"  # 0.005 km per bit, total_increasing
    coordinator = _coordinator({})
    sensor = WialonParamSensor(coordinator, UNIT_ID, {"nm": "Unit"}, key, 1)

    def report(raw: int) -> object:
        coordinator.data[UNIT_ID]["prms"] = {key: {"v": raw, "ct": 1, "at": 1}}
        return sensor.native_value

    assert report(0) is None
    assert report(2469000) == pytest.approx(12345.0)
    assert report(0) == pytest.approx(12345.0)
    assert report(2469200) == pytest.approx(12346.0)
