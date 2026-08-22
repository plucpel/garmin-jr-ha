"""Garmin Jr Integration for Home Assistant."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    AUTH_TYPE_TOKEN,
    CONF_AUTH_TYPE,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_TOKEN_DATA,
    DOMAIN,
    LOGGER,
    PLATFORMS,
)
from .coordinator import GarminJrDataUpdateCoordinator
from .garmin_client import GarminJrClient

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the Garmin Jr component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Garmin Jr from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    token_data = entry.data.get(CONF_TOKEN_DATA)
    email = entry.data.get(CONF_EMAIL)
    password = entry.data.get(CONF_PASSWORD)

    client = GarminJrClient(
        email=email,
        password=password,
        token_data=token_data,
    )

    coordinator = GarminJrDataUpdateCoordinator(hass, entry, client)

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
