"""Binary sensor platform for Wialon (Xirgo boolean sensors)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WialonConfigEntry
from .api import parse_params
from .coordinator import WialonCoordinator
from .entity import WialonEntity
from .params import ResolvedParam, resolve


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WialonConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one binary sensor per boolean Xirgo parameter."""
    coordinator = entry.runtime_data.coordinator
    param_keys: dict[int, set[str]] = {}

    def _add_new() -> None:
        new: list[WialonParamBinarySensor] = []
        for unit_id, item in (coordinator.data or {}).items():
            if item is None:
                continue
            keys = param_keys.setdefault(unit_id, set())
            for key in parse_params(item.get("lmsg")):
                if key in keys:
                    continue
                resolved = resolve(key)
                if resolved is None or not resolved.is_binary:
                    continue
                keys.add(key)
                new.append(
                    WialonParamBinarySensor(coordinator, unit_id, item, key, resolved)
                )
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class WialonParamBinarySensor(WialonEntity, BinarySensorEntity):
    """Binary sensor for one boolean device parameter."""

    def __init__(
        self,
        coordinator: WialonCoordinator,
        unit_id: int,
        item: dict,
        key: str,
        resolved: ResolvedParam,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, unit_id, item)
        self._key = key
        self._resolved = resolved
        self._attr_unique_id = f"{unit_id}_param_{key}"
        self._attr_name = resolved.name
        self._attr_entity_registry_enabled_default = resolved.enabled_default
        if resolved.binary_device_class:
            self._attr_device_class = BinarySensorDeviceClass(
                resolved.binary_device_class
            )
        if resolved.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        """State from the latest message; None when absent or not numeric."""
        value: Any = parse_params(self.lmsg).get(self._key)
        if value is None or not isinstance(value, (bool, int, float)):
            return None
        return bool(value) != self._resolved.invert
