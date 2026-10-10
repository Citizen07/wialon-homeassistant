"""Diagnostics for Wialon."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import WialonConfigEntry
from .const import CONF_TOKEN

TO_REDACT = {CONF_TOKEN}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WialonConfigEntry
) -> dict[str, Any]:
    """Return diagnostics, including raw latest unit data (shows device params)."""
    coordinator = entry.runtime_data.coordinator
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "units": {str(k): v for k, v in (coordinator.data or {}).items()},
    }
