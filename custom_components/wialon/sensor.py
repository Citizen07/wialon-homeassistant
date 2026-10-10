"""Sensor platform for Wialon."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import DEGREE, EntityCategory, UnitOfLength, UnitOfSpeed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WialonConfigEntry
from .api import message_time, parse_params, parse_position
from .const import DEFAULT_ENABLED_PARAMS
from .coordinator import WialonCoordinator
from .entity import WialonEntity

MAX_STATE_LENGTH = 255


def _pos_value(field: str) -> Callable[[dict | None], Any]:
    def _get(lmsg: dict | None) -> Any:
        pos = parse_position(lmsg)
        return pos[field] if pos else None

    return _get


@dataclass(frozen=True, kw_only=True)
class WialonSensorDescription(SensorEntityDescription):
    """Description of a fixed per-unit sensor."""

    value_fn: Callable[[dict | None], Any]


SENSORS: tuple[WialonSensorDescription, ...] = (
    WialonSensorDescription(
        key="speed",
        translation_key="speed",
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_pos_value("speed"),
    ),
    WialonSensorDescription(
        key="course",
        translation_key="course",
        native_unit_of_measurement=DEGREE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_pos_value("course"),
    ),
    WialonSensorDescription(
        key="altitude",
        translation_key="altitude",
        native_unit_of_measurement=UnitOfLength.METERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_pos_value("altitude"),
    ),
    WialonSensorDescription(
        key="satellites",
        translation_key="satellites",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_pos_value("satellites"),
    ),
    WialonSensorDescription(
        key="last_message",
        translation_key="last_message",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=message_time,
    ),
)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WialonConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up fixed and per-parameter sensors."""
    coordinator = entry.runtime_data.coordinator
    fixed_added: set[int] = set()
    param_keys: dict[int, set[str]] = {}

    def _add_new() -> None:
        new: list[SensorEntity] = []
        for unit_id, item in (coordinator.data or {}).items():
            if item is None:
                continue
            if unit_id not in fixed_added:
                fixed_added.add(unit_id)
                new.extend(
                    WialonSensor(coordinator, unit_id, item, desc) for desc in SENSORS
                )
            keys = param_keys.setdefault(unit_id, set())
            for key, value in parse_params(item.get("lmsg")).items():
                if key not in keys:
                    keys.add(key)
                    new.append(
                        WialonParamSensor(coordinator, unit_id, item, key, value)
                    )
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class WialonSensor(WialonEntity, SensorEntity):
    """Fixed sensor of a unit."""

    entity_description: WialonSensorDescription

    def __init__(
        self,
        coordinator: WialonCoordinator,
        unit_id: int,
        item: dict,
        description: WialonSensorDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, unit_id, item)
        self.entity_description = description
        self._attr_unique_id = f"{unit_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        """Value from the latest message."""
        return self.entity_description.value_fn(self.lmsg)


class WialonParamSensor(WialonEntity, SensorEntity):
    """Sensor for one raw device parameter."""

    def __init__(
        self,
        coordinator: WialonCoordinator,
        unit_id: int,
        item: dict,
        key: str,
        sample: Any,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, unit_id, item)
        self._key = key
        self._numeric = _is_number(sample)
        self._attr_unique_id = f"{unit_id}_p_{key}"
        self._attr_name = key
        self._attr_entity_registry_enabled_default = key in DEFAULT_ENABLED_PARAMS
        if self._numeric:
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> Any:
        """Raw parameter value; None when absent or of a different type."""
        value = parse_params(self.lmsg).get(self._key)
        if value is None:
            return None
        if self._numeric:
            return value if _is_number(value) else None
        return str(value)[:MAX_STATE_LENGTH]
