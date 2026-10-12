"""Device tracker tests; need Home Assistant (CI installs it via PHCC)."""

from __future__ import annotations

from datetime import timedelta
import logging
from pathlib import Path
import sys

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from freezegun.api import FrozenDateTimeFactory  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.helpers.update_coordinator import (  # noqa: E402
    DataUpdateCoordinator,
)
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
    MockEntityPlatform,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from custom_components.wialon.const import DOMAIN  # noqa: E402
from custom_components.wialon.device_tracker import WialonTracker  # noqa: E402

UNIT_ID = 1
ITEM = {"id": UNIT_ID, "nm": "Unit"}


async def _tracker(hass: HomeAssistant) -> tuple[DataUpdateCoordinator, str]:
    """A tracker on a bare coordinator, added to a mock platform."""
    coordinator = DataUpdateCoordinator(
        hass,
        logging.getLogger(__name__),
        config_entry=MockConfigEntry(domain=DOMAIN, data={}),
        name="test",
    )
    coordinator.last_position = {UNIT_ID: {"latitude": 10.0, "longitude": 20.0}}
    coordinator.async_set_updated_data({UNIT_ID: ITEM})
    tracker = WialonTracker(coordinator, UNIT_ID, ITEM)
    platform = MockEntityPlatform(hass, domain="device_tracker", platform_name=DOMAIN)
    await platform.async_add_entities([tracker])
    await hass.async_block_till_done()
    return coordinator, tracker.entity_id


def test_force_update_off() -> None:
    """Coordinator trackers do not inherit TrackerEntity's forced writes."""
    assert WialonTracker.force_update.fget(None) is False


async def test_unchanged_position_writes_no_state(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Polls with the same position leave last_changed and last_updated."""
    coordinator, entity_id = await _tracker(hass)
    before = hass.states.get(entity_id)
    assert before.attributes["latitude"] == 10.0

    for _ in range(2):
        freezer.tick(timedelta(seconds=60))
        coordinator.async_set_updated_data({UNIT_ID: dict(ITEM)})
        await hass.async_block_till_done()

    after = hass.states.get(entity_id)
    assert after.state == before.state
    assert after.last_changed == before.last_changed
    assert after.last_updated == before.last_updated


async def test_moved_position_updates_attributes(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A new position still writes the state with the new coordinates."""
    coordinator, entity_id = await _tracker(hass)
    before = hass.states.get(entity_id)

    freezer.tick(timedelta(seconds=60))
    coordinator.last_position[UNIT_ID] = {"latitude": 10.5, "longitude": 20.5}
    coordinator.async_set_updated_data({UNIT_ID: dict(ITEM)})
    await hass.async_block_till_done()

    after = hass.states.get(entity_id)
    assert after.attributes["latitude"] == 10.5
    assert after.attributes["longitude"] == 20.5
    assert after.last_updated > before.last_updated
