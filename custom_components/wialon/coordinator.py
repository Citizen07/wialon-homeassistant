"""Data update coordinator for Wialon."""

from __future__ import annotations

from datetime import date, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    WINDOW_DROP,
    WINDOW_FETCH,
    WINDOW_NONE,
    MessageWindow,
    WialonAuthError,
    WialonClient,
    WialonError,
    build_window,
    parse_position,
    window_action,
)
from .const import CONF_SCAN_INTERVAL, CONF_UNITS, DEFAULT_SCAN_INTERVAL, DOMAIN
from .track import Point, Track, build_track, extract_points, merge

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
        # GPS track per unit; in memory only, today's is reloaded after a restart.
        self.tracks: dict[int, Track] = {}
        self._track_points: dict[int, list[Point]] = {}
        self._track_day: dict[int, date] = {}
        self._track_live: dict[int, bool] = {}
        self._track_reload: set[int] = set()
        self._track_gen: dict[int, int] = {}

    async def _async_update_data(self) -> dict[int, dict | None]:
        try:
            data = await self.client.get_units(self.unit_ids)
        except WialonAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except WialonError as err:
            raise UpdateFailed(str(err)) from err
        for unit_id, item in data.items():
            if item is None:
                if unit_id in self.unit_ids:
                    await self._update_track(unit_id, None, None, False)
                continue
            # Fall back to the item-level `pos` (last known position, same shape
            # as lmsg.pos) so a fix-less message does not blank the tracker,
            # even right after a restart.
            pos = parse_position(item.get("lmsg")) or parse_position(item)
            if pos is not None:
                self.last_position[unit_id] = pos
            action, messages, failed = await self._update_window(unit_id, item)
            if unit_id in self.unit_ids:
                await self._update_track(unit_id, action, messages, failed)
        return data

    async def _update_window(
        self, unit_id: int, item: dict
    ) -> tuple[str, list[dict] | None, bool]:
        """Read the messages since the previous poll; never fails the poll.

        Returns the window action, the messages loaded (None when none were
        fetched) and whether the fetch failed.
        """
        lmsg = item.get("lmsg")
        new_t = lmsg.get("t") if isinstance(lmsg, dict) else None
        if isinstance(new_t, bool) or not isinstance(new_t, int):
            return WINDOW_NONE, None, False
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
                return action, None, True
            window = build_window(messages)
            if window is not None:
                self.windows[unit_id] = window
            return action, messages, False
        return action, None, False

    def track_date(self, unit_id: int) -> date | None:
        """The local date the shown track covers."""
        return self._track_day.get(unit_id)

    @staticmethod
    def _day_bounds(day: date) -> tuple[int, int]:
        """UTC epoch seconds of the first and last second of a local day."""
        start = dt_util.start_of_local_day(day)
        end = dt_util.start_of_local_day(day + timedelta(days=1))
        return int(start.timestamp()), int(end.timestamp()) - 1

    async def _build(self, points: list[Point]) -> Track:
        """Build off the event loop: a day can hold thousands of positions."""
        return await self.hass.async_add_executor_job(build_track, points)

    def _store_track(
        self, unit_id: int, day: date, points: list[Point], track: Track
    ) -> None:
        self._track_points[unit_id] = points
        self._track_day[unit_id] = day
        self.tracks[unit_id] = track
        _LOGGER.debug(
            "Track of unit %s for %s: %s positions, %s kept, tolerance %s m, %s km",
            unit_id,
            day,
            track.points,
            track.kept,
            track.tolerance,
            track.distance_km,
        )

    async def _update_track(
        self,
        unit_id: int,
        action: str | None,
        messages: list[dict] | None,
        failed: bool,
    ) -> None:
        """Keep today's track current; never fails the poll."""
        if not self._track_live.get(unit_id, True):
            return  # a past day is frozen
        now = dt_util.now()
        today = now.date()
        if action == WINDOW_DROP or failed:
            self._track_reload.add(unit_id)
        gen = self._track_gen.get(unit_id, 0)
        start, _ = self._day_bounds(today)
        if self._track_day.get(unit_id) != today or unit_id in self._track_reload:
            try:
                loaded = await self.client.load_messages(
                    unit_id, start, int(now.timestamp())
                )
            except WialonError as err:
                _LOGGER.debug("Track of unit %s failed to load: %s", unit_id, err)
                self._track_reload.add(unit_id)
                return
            points = extract_points(loaded)
        elif messages:
            existing = self._track_points.get(unit_id, [])
            points = merge(
                existing, [p for p in extract_points(messages) if p[0] >= start]
            )
            if len(points) == len(existing):
                return
        else:
            return
        track = await self._build(points)
        if self._track_gen.get(unit_id, 0) != gen or not self._track_live.get(
            unit_id, True
        ):
            return  # a date was selected while this was loading
        self._track_reload.discard(unit_id)
        self._store_track(unit_id, today, points, track)

    async def async_set_track_date(self, unit_id: int, day: date) -> None:
        """Show the track of a local day; today follows the live track."""
        now = dt_util.now()
        today = now.date()
        if day > today:
            raise ServiceValidationError("The track date cannot be in the future")
        gen = self._track_gen.get(unit_id, 0) + 1
        self._track_gen[unit_id] = gen
        start, end = self._day_bounds(day)
        if day == today:
            end = int(now.timestamp())
        try:
            loaded = await self.client.load_messages(unit_id, start, end)
        except WialonError as err:
            raise HomeAssistantError(f"Could not load the track: {err}") from err
        points = extract_points(loaded)
        track = await self._build(points)
        if self._track_gen.get(unit_id) != gen:
            return  # a later selection wins
        self._track_live[unit_id] = day == today
        self._track_reload.discard(unit_id)
        self._store_track(unit_id, day, points, track)
        self.async_update_listeners()
