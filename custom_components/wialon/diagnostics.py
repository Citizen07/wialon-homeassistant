"""Diagnostics for Wialon."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import WialonConfigEntry
from .const import CONF_TOKEN, CONF_USER_NAME

TO_REDACT = {CONF_TOKEN, CONF_USER_NAME}
# Unit fields that identify the vehicle, its tracker or its location. Redaction
# is recursive, so "pos" also covers lmsg.pos. Params in lmsg.p and prms stay:
# they are what diagnostics are for (a param named like a field above is
# redacted too, which errs on the safe side).
UNIT_REDACT = {"nm", "uid", "uid2", "ph", "ph2", "psw", "pos"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WialonConfigEntry
) -> dict[str, Any]:
    """Return diagnostics, including raw latest unit data (shows device params)."""
    coordinator = entry.runtime_data.coordinator
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "units": {
            str(k): async_redact_data(v, UNIT_REDACT)
            for k, v in (coordinator.data or {}).items()
        },
    }
