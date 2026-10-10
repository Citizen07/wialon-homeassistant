"""Base entity for Wialon units."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_WEB_URL, DOMAIN
from .coordinator import WialonCoordinator


class WialonEntity(CoordinatorEntity[WialonCoordinator]):
    """An entity belonging to one Wialon unit (one HA device per unit)."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: WialonCoordinator, unit_id: int, item: dict
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self.unit_id = unit_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(unit_id))},
            name=item.get("nm") or str(unit_id),
            manufacturer="Wialon",
            serial_number=item.get("uid"),
            configuration_url=coordinator.config_entry.data.get(CONF_WEB_URL),
        )

    @property
    def unit(self) -> dict | None:
        """Latest raw unit item."""
        return (self.coordinator.data or {}).get(self.unit_id)

    @property
    def lmsg(self) -> dict | None:
        """Latest message of the unit."""
        unit = self.unit
        return unit.get("lmsg") if unit else None

    @property
    def available(self) -> bool:
        """Available when the update worked and the unit still exists."""
        return super().available and self.unit is not None
