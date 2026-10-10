"""Data update coordinator for Wialon."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import WialonAuthError, WialonClient, WialonError, parse_position
from .const import CONF_SCAN_INTERVAL, CONF_UNITS, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class WialonCoordinator(DataUpdateCoordinator[dict[int, dict | None]]):
    """Poll the selected units in one batch."""

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: WialonClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.client = client
        self.unit_ids = [int(u) for u in entry.options.get(CONF_UNITS, [])]
        self.last_position: dict[int, dict] = {}

    async def _async_update_data(self) -> dict[int, dict | None]:
        try:
            data = await self.client.get_units(self.unit_ids)
        except WialonAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except WialonError as err:
            raise UpdateFailed(str(err)) from err
        for unit_id, item in data.items():
            if item is None:
                continue
            # Fall back to the item-level `pos` (last known position, same shape
            # as lmsg.pos) so a fix-less message does not blank the tracker,
            # even right after a restart.
            pos = parse_position(item.get("lmsg")) or parse_position(item)
            if pos is not None:
                self.last_position[unit_id] = pos
        return data
