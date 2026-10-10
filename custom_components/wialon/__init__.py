"""The Wialon integration."""

from __future__ import annotations

from dataclasses import dataclass
import re

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import WialonAuthError, WialonClient, WialonConnectionError, WialonError
from .const import CONF_API_URL, CONF_TOKEN, CONF_UNITS, DOMAIN
from .coordinator import WialonCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.DEVICE_TRACKER, Platform.SENSOR]


@dataclass
class WialonData:
    """Runtime data."""

    client: WialonClient
    coordinator: WialonCoordinator


type WialonConfigEntry = ConfigEntry[WialonData]


_OLD_PARAM_UID = re.compile(r"^\d+_p_")


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Drop parameter entities from before the `_param_` unique_id scheme.

    Minor 1 named them after the raw key. Removing them and reusing the same
    unique_id (minor 2) does not help: HA restores a deleted entity's old
    entity_id and disabled_by when its unique_id comes back. So minor 3 moved
    the parameter entities to `{unit}_param_{key}` and drops every `{unit}_p_`
    entry; they are recreated under the new ids with the current defaults.
    """
    if entry.version == 1 and entry.minor_version < 3:
        registry = er.async_get(hass)
        for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
            if _OLD_PARAM_UID.match(reg_entry.unique_id):
                registry.async_remove(reg_entry.entity_id)
        hass.config_entries.async_update_entry(entry, minor_version=3)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: WialonConfigEntry) -> bool:
    """Set up Wialon from a config entry."""
    client = WialonClient(
        async_get_clientsession(hass), entry.data[CONF_API_URL], entry.data[CONF_TOKEN]
    )
    try:
        await client.login()
    except WialonAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except (WialonConnectionError, WialonError) as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = WialonCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = WialonData(client, coordinator)

    selected = {str(u) for u in entry.options.get(CONF_UNITS, [])}
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        ids = {i[1] for i in device.identifiers if i[0] == DOMAIN}
        if ids and not ids & selected:
            registry.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: WialonConfigEntry) -> None:
    """Reload on options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: WialonConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.logout()
    return unloaded


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: WialonConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing devices of units that are no longer selected."""
    selected = {str(u) for u in entry.options.get(CONF_UNITS, [])}
    return not any(
        domain == DOMAIN and ident in selected
        for domain, ident in device_entry.identifiers
    )
