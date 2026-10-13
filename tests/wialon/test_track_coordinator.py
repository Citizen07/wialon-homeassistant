"""Track handling of the coordinator; needs Home Assistant (CI installs it via PHCC)."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path
import sys

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from freezegun.api import FrozenDateTimeFactory  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.exceptions import (  # noqa: E402
    HomeAssistantError,
    ServiceValidationError,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from custom_components.wialon.api import MAX_WINDOW, WialonError  # noqa: E402
from custom_components.wialon.const import (  # noqa: E402
    CONF_SCAN_INTERVAL,
    CONF_UNITS,
    DOMAIN,
)
from custom_components.wialon.coordinator import WialonCoordinator  # noqa: E402

UNIT = 1
NOW = datetime.fromisoformat("2026-03-10T12:00:00+00:00")
TODAY = date(2026, 3, 10)
YESTERDAY = date(2026, 3, 9)


def ts(day: str, hhmm: str) -> int:
    return int(datetime.fromisoformat(f"{day}T{hhmm}:00+00:00").timestamp())


def drive(t0: int, n: int, lat0: float = 10.0) -> list[dict]:
    """n messages 5 s apart on a straight northbound line (about 55 m apart)."""
    return [
        {"t": t0 + 5 * i, "pos": {"x": 20.0, "y": lat0 + 0.0005 * i, "sc": 9}}
        for i in range(n)
    ]


class FakeClient:
    """Stands in for WialonClient: the units and a message store."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.last_t: int | None = None
        self.fail = False
        self.calls: list[tuple[int, int, int]] = []
        self.gate: asyncio.Event | None = None  # blocks the next load once

    async def get_units(self, unit_ids):
        lmsg = {"t": self.last_t} if self.last_t is not None else None
        item = {"id": UNIT, "nm": "Unit"}
        if lmsg:
            item["lmsg"] = lmsg
        return {UNIT: item}

    async def load_messages(self, unit_id, time_from, time_to):
        self.calls.append((unit_id, time_from, time_to))
        gate, self.gate = self.gate, None
        if gate is not None:
            await gate.wait()
        if self.fail:
            raise WialonError("boom")
        return [m for m in self.messages if time_from <= m["t"] <= time_to]

    def add(self, messages: list[dict]) -> None:
        self.messages += messages
        self.last_t = max(m["t"] for m in self.messages)

    def full_loads(self, day: str = "2026-03-10") -> list[tuple[int, int, int]]:
        start = ts(day, "00:00")
        return [c for c in self.calls if c[1] == start]


async def _setup(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, now: str = "12:00"
) -> tuple[WialonCoordinator, FakeClient]:
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to(f"2026-03-10T{now}:00+00:00")
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, options={CONF_UNITS: [UNIT], CONF_SCAN_INTERVAL: 60}
    )
    entry.add_to_hass(hass)
    client = FakeClient()
    return WialonCoordinator(hass, entry, client), client


async def test_first_refresh_loads_today_in_full(hass, freezer) -> None:
    coordinator, client = await _setup(hass, freezer)
    client.add(drive(ts("2026-03-10", "11:00"), 100))
    client.add(drive(ts("2026-03-09", "10:00"), 10))  # yesterday: not in the track

    await coordinator._async_update_data()

    assert client.calls == [(UNIT, ts("2026-03-10", "00:00"), int(NOW.timestamp()))]
    track = coordinator.tracks[UNIT]
    assert track.points == 100
    assert track.geojson is not None
    assert coordinator.track_date(UNIT) == TODAY


async def test_next_poll_appends_without_reload(hass, freezer) -> None:
    coordinator, client = await _setup(hass, freezer)
    client.add(drive(ts("2026-03-10", "11:00"), 100))
    await coordinator._async_update_data()

    freezer.tick(timedelta(seconds=60))
    client.add(drive(ts("2026-03-10", "11:50"), 20, lat0=10.1))
    await coordinator._async_update_data()

    assert len(client.full_loads()) == 1
    assert coordinator.tracks[UNIT].points == 120


async def test_poll_without_new_positions_keeps_track(hass, freezer) -> None:
    coordinator, client = await _setup(hass, freezer)
    client.add(drive(ts("2026-03-10", "11:00"), 100))
    await coordinator._async_update_data()
    track = coordinator.tracks[UNIT]

    freezer.tick(timedelta(seconds=60))
    await coordinator._async_update_data()

    assert coordinator.tracks[UNIT] is track
    assert len(client.calls) == 1


async def test_drop_gap_reloads_in_full(hass, freezer) -> None:
    coordinator, client = await _setup(hass, freezer, now="23:00")
    client.add(drive(ts("2026-03-10", "01:00"), 50))
    await coordinator._async_update_data()
    assert len(client.full_loads()) == 1

    freezer.tick(timedelta(minutes=30))
    # The latest message is further past the previous one than a window may span.
    assert ts("2026-03-10", "22:00") - client.last_t > MAX_WINDOW
    client.add(drive(ts("2026-03-10", "22:00"), 30, lat0=10.2))
    await coordinator._async_update_data()

    assert len(client.full_loads()) == 2
    assert coordinator.tracks[UNIT].points == 80


async def test_load_failure_keeps_track_and_retries(hass, freezer) -> None:
    coordinator, client = await _setup(hass, freezer)
    client.add(drive(ts("2026-03-10", "11:00"), 100))
    await coordinator._async_update_data()
    track = coordinator.tracks[UNIT]

    freezer.tick(timedelta(seconds=60))
    client.add(drive(ts("2026-03-10", "11:50"), 20, lat0=10.1))
    client.fail = True
    await coordinator._async_update_data()  # does not raise
    assert coordinator.tracks[UNIT] is track

    freezer.tick(timedelta(seconds=60))
    client.fail = False
    await coordinator._async_update_data()

    assert len(client.full_loads()) == 3  # first, failed, retried
    assert coordinator.tracks[UNIT].points == 120


async def test_midnight_rolls_over_to_the_new_day(hass, freezer) -> None:
    coordinator, client = await _setup(hass, freezer, now="23:50")
    client.add(drive(ts("2026-03-10", "23:00"), 50))
    await coordinator._async_update_data()
    assert coordinator.tracks[UNIT].points == 50

    freezer.tick(timedelta(minutes=20))  # 00:10 on the 11th
    client.add(drive(ts("2026-03-11", "00:05"), 10, lat0=10.3))
    await coordinator._async_update_data()

    assert coordinator.track_date(UNIT) == date(2026, 3, 11)
    assert len(client.full_loads("2026-03-11")) == 1
    assert coordinator.tracks[UNIT].points == 10


async def test_no_data_unit_loads_once_not_every_poll(hass, freezer) -> None:
    coordinator, client = await _setup(hass, freezer)

    await coordinator._async_update_data()
    await coordinator._async_update_data()

    assert len(client.calls) == 1
    assert coordinator.tracks[UNIT].geojson is None
    assert coordinator.tracks[UNIT].distance_km == 0.0


async def test_past_date_freezes_and_future_raises(hass, freezer) -> None:
    coordinator, client = await _setup(hass, freezer)
    client.add(drive(ts("2026-03-09", "10:00"), 40))
    client.add(drive(ts("2026-03-10", "11:00"), 100))
    await coordinator._async_update_data()

    await coordinator.async_set_track_date(UNIT, YESTERDAY)
    assert coordinator.track_date(UNIT) == YESTERDAY
    assert coordinator.tracks[UNIT].points == 40
    assert client.calls[-1] == (
        UNIT,
        ts("2026-03-09", "00:00"),
        ts("2026-03-10", "00:00") - 1,
    )

    freezer.tick(timedelta(seconds=60))
    client.add(drive(ts("2026-03-10", "11:50"), 20, lat0=10.1))
    calls = len(client.calls)
    await coordinator._async_update_data()

    assert coordinator.track_date(UNIT) == YESTERDAY
    assert coordinator.tracks[UNIT].points == 40
    assert all(c[1] != ts("2026-03-10", "00:00") for c in client.calls[calls:])

    with pytest.raises(ServiceValidationError):
        await coordinator.async_set_track_date(UNIT, date(2026, 3, 11))
    assert coordinator.track_date(UNIT) == YESTERDAY


async def test_failed_selection_keeps_previous_track(hass, freezer) -> None:
    coordinator, client = await _setup(hass, freezer)
    client.add(drive(ts("2026-03-10", "11:00"), 100))
    await coordinator._async_update_data()
    track = coordinator.tracks[UNIT]

    client.fail = True
    with pytest.raises(HomeAssistantError):
        await coordinator.async_set_track_date(UNIT, YESTERDAY)

    assert coordinator.tracks[UNIT] is track
    assert coordinator.track_date(UNIT) == TODAY


async def test_selecting_today_goes_live_again(hass, freezer) -> None:
    coordinator, client = await _setup(hass, freezer)
    client.add(drive(ts("2026-03-09", "10:00"), 40))
    client.add(drive(ts("2026-03-10", "11:00"), 100))
    await coordinator._async_update_data()
    await coordinator.async_set_track_date(UNIT, YESTERDAY)

    await coordinator.async_set_track_date(UNIT, TODAY)
    assert coordinator.track_date(UNIT) == TODAY
    assert coordinator.tracks[UNIT].points == 100

    freezer.tick(timedelta(seconds=60))
    client.add(drive(ts("2026-03-10", "11:50"), 20, lat0=10.1))
    await coordinator._async_update_data()
    assert coordinator.tracks[UNIT].points == 120


async def test_selection_notifies_listeners(hass, freezer) -> None:
    coordinator, client = await _setup(hass, freezer)
    client.add(drive(ts("2026-03-09", "10:00"), 40))
    seen: list[date | None] = []
    coordinator.async_add_listener(lambda: seen.append(coordinator.track_date(UNIT)))

    await coordinator.async_set_track_date(UNIT, YESTERDAY)

    assert seen == [YESTERDAY]


async def test_stale_poll_load_is_discarded(hass, freezer) -> None:
    coordinator, client = await _setup(hass, freezer)
    client.add(drive(ts("2026-03-09", "10:00"), 40))
    client.add(drive(ts("2026-03-10", "11:00"), 100))
    gate = client.gate = asyncio.Event()  # the poll's full load of today waits

    poll = hass.async_create_task(coordinator._async_update_data())
    for _ in range(10):
        await asyncio.sleep(0)
    assert client.gate is None  # the poll's load is the one waiting
    assert not poll.done()

    await coordinator.async_set_track_date(UNIT, YESTERDAY)
    gate.set()
    await poll

    assert coordinator.track_date(UNIT) == YESTERDAY
    assert coordinator.tracks[UNIT].points == 40
