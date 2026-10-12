"""Device tracker platform for Wialon."""

from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
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
    """Set up one tracker per selected unit."""
    coordinator = entry.runtime_data.coordinator
    added: set[int] = set()

    def _add_new() -> None:
        new = []
        for unit_id, item in (coordinator.data or {}).items():
            if item is not None and unit_id not in added:
                added.add(unit_id)
                new.append(WialonTracker(coordinator, unit_id, item))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class WialonTracker(WialonEntity, TrackerEntity):
    """GPS tracker for a unit."""

    _attr_name = None
    _attr_source_type = SourceType.GPS
    _attr_location_accuracy = 0

    def __init__(
        self, coordinator: WialonCoordinator, unit_id: int, item: dict
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, unit_id, item)
        self._attr_unique_id = f"{unit_id}_tracker"

    @property
    def force_update(self) -> bool:
        """Write only changes, unlike push trackers where every report counts.

        An unchanged position leaves last_changed alone and adds no recorder
        row; a moving unit changes its coordinates, which writes anyway.
        """
        return False

    def _position(self) -> dict | None:
        return self.coordinator.last_position.get(self.unit_id)

    @property
    def latitude(self) -> float | None:
        """Latitude."""
        pos = self._position()
        return pos["latitude"] if pos else None

    @property
    def longitude(self) -> float | None:
        """Longitude."""
        pos = self._position()
        return pos["longitude"] if pos else None
