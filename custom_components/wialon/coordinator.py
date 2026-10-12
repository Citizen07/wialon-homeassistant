"""Data update coordinator for Wialon."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    WINDOW_DROP,
    WINDOW_FETCH,
    MessageWindow,
    WialonAuthError,
    WialonClient,
    WialonError,
    build_window,
    parse_position,
    window_action,
)
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
        # Messages since the previous poll, per unit; in memory only.
        self.windows: dict[int, MessageWindow] = {}
        self._last_t: dict[int, int] = {}

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
            await self._update_window(unit_id, item)
        return data

    async def _update_window(self, unit_id: int, item: dict) -> None:
        """Read the messages since the previous poll; never fails the poll."""
        lmsg = item.get("lmsg")
        new_t = lmsg.get("t") if isinstance(lmsg, dict) else None
        if isinstance(new_t, bool) or not isinstance(new_t, int):
            return
        prev_t = self._last_t.get(unit_id)
        action = window_action(prev_t, new_t)
        self._last_t[unit_id] = max(new_t, prev_t or new_t)
        if action == WINDOW_DROP:
            self.windows.pop(unit_id, None)
        elif action == WINDOW_FETCH:
            try:
                messages = await self.client.load_messages(unit_id, prev_t + 1, new_t)
            except WialonError as err:
                _LOGGER.debug("Message window of unit %s failed: %s", unit_id, err)
                return
            window = build_window(messages)
            if window is not None:
                self.windows[unit_id] = window
