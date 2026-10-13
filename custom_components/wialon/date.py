"""Date platform for Wialon: which day the track shows."""

from __future__ import annotations

from datetime import date

from homeassistant.components.date import DateEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WialonConfigEntry
from .coordinator import WialonCoordinator
from .entity import WialonEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WialonConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one track date per selected unit."""
    coordinator = entry.runtime_data.coordinator
    added: set[int] = set()

    def _add_new() -> None:
        new = []
        for unit_id, item in (coordinator.data or {}).items():
            if item is not None and unit_id not in added:
                added.add(unit_id)
                new.append(WialonTrackDate(coordinator, unit_id, item))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class WialonTrackDate(WialonEntity, DateEntity):
    """The day whose track the track sensor shows."""

    _attr_translation_key = "track_date"

    def __init__(
        self, coordinator: WialonCoordinator, unit_id: int, item: dict
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, unit_id, item)
        self._attr_unique_id = f"{unit_id}_track_date"

    @property
    def native_value(self) -> date | None:
        """The day the track covers."""
        return self.coordinator.track_date(self.unit_id)

    async def async_set_value(self, value: date) -> None:
        """Show the track of another day; today goes live again."""
        await self.coordinator.async_set_track_date(self.unit_id, value)
