"""Track sensor and track date entity tests; need Home Assistant (CI has PHCC)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from custom_components.wialon.date import WialonTrackDate  # noqa: E402
from custom_components.wialon.sensor import WialonTrackSensor  # noqa: E402
from custom_components.wialon.track import Track  # noqa: E402

UNIT_ID = 1
DAY = date(2026, 3, 10)
GEOJSON = {"type": "MultiLineString", "coordinates": [[[20.0, 10.0], [20.0, 10.1]]]}


def _coordinator(track: Track | None, day: date | None) -> SimpleNamespace:
    return SimpleNamespace(
        data={UNIT_ID: {"id": UNIT_ID}},
        tracks={UNIT_ID: track} if track else {},
        track_date=lambda unit_id: day,
        async_set_track_date=AsyncMock(),
        config_entry=SimpleNamespace(data={}),
        last_update_success=True,
    )


def test_track_sensor_value_and_attributes() -> None:
    track = Track(GEOJSON, 12.34, 500, 2, 5.0)
    sensor = WialonTrackSensor(_coordinator(track, DAY), UNIT_ID, {"nm": "Unit"})

    assert sensor.native_value == 12.34
    assert sensor.extra_state_attributes == {
        "date": "2026-03-10",
        "geo_location": GEOJSON,
    }
    assert sensor.unique_id == "1_track"
    assert sensor.state_class is None


def test_track_sensor_before_a_track_exists() -> None:
    sensor = WialonTrackSensor(_coordinator(None, None), UNIT_ID, {"nm": "Unit"})

    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {"date": None, "geo_location": None}


def test_track_sensor_without_geometry() -> None:
    track = Track(None, 0.0, 0, 0, 5.0)
    sensor = WialonTrackSensor(_coordinator(track, DAY), UNIT_ID, {"nm": "Unit"})

    assert sensor.native_value == 0.0
    assert sensor.extra_state_attributes["geo_location"] is None


def test_geo_location_is_not_recorded() -> None:
    assert "geo_location" in WialonTrackSensor._unrecorded_attributes


def test_track_date_value() -> None:
    entity = WialonTrackDate(_coordinator(None, DAY), UNIT_ID, {"nm": "Unit"})

    assert entity.native_value == DAY
    assert entity.unique_id == "1_track_date"
    assert (
        WialonTrackDate(_coordinator(None, None), UNIT_ID, {"nm": "Unit"}).native_value
        is None
    )


async def test_track_date_set_value_delegates() -> None:
    coordinator = _coordinator(None, DAY)
    entity = WialonTrackDate(coordinator, UNIT_ID, {"nm": "Unit"})

    await entity.async_set_value(date(2026, 3, 9))

    coordinator.async_set_track_date.assert_awaited_once_with(UNIT_ID, date(2026, 3, 9))
