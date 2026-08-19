"""Home Assistant integration for MAIKA smart speakers."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MaikaApiClient
from .const import (
    CONF_CLIENT_ID,
    CONF_PASSWORD,
    CONF_PHONE_NUMBER,
    CONF_SESSION_ID,
    DOMAIN,
)
from .coordinator import MaikaDataUpdateCoordinator
from .phone import normalize_login_identifier
from .runtime import MaikaRuntimeData

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.MEDIA_PLAYER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
]

type MaikaConfigEntry = ConfigEntry[MaikaRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: MaikaConfigEntry) -> bool:
    """Set up MAIKA from a config entry."""
    client = MaikaApiClient(
        async_get_clientsession(hass),
        str(entry.data[CONF_PHONE_NUMBER]),
        str(entry.data[CONF_PASSWORD]),
        str(entry.data[CONF_CLIENT_ID]),
        str(entry.data[CONF_SESSION_ID]),
    )
    coordinator = MaikaDataUpdateCoordinator(hass, entry, client)
    client.set_event_callback(coordinator.async_handle_stream_frame)
    listener_task = entry.async_create_background_task(
        hass,
        client.async_listen_forever(),
        "MAIKA cloud event stream",
    )
    client.set_listener_task(listener_task)

    try:
        await coordinator.async_config_entry_first_refresh()
        entry.runtime_data = MaikaRuntimeData(client, coordinator)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await client.async_stop()
        raise

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MaikaConfigEntry) -> bool:
    """Unload a MAIKA config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.coordinator.async_stop_voice_subscriptions()
        await entry.runtime_data.client.async_stop()
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: MaikaConfigEntry) -> bool:
    """Migrate config entries created before activation support."""
    if entry.version < 3:
        data = dict(entry.data)
        phone_number = data.get(CONF_PHONE_NUMBER)
        if phone_number is not None:
            data[CONF_PHONE_NUMBER] = normalize_login_identifier(str(phone_number))
        hass.config_entries.async_update_entry(entry, data=data, version=3)
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: MaikaConfigEntry) -> None:
    """Reload after options change."""
    await hass.config_entries.async_reload(entry.entry_id)
