"""Diagnostics support for Garmin Jr."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import GarminJrDataUpdateCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics and discovery probe for Garmin Jr config entry."""
    coordinator: GarminJrDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    client = coordinator.client

    probe_data = await hass.async_add_executor_job(client._probe_endpoints)

    return {
        "tokens": {
            "di_token": client.client.di_token,
            "di_refresh_token": client.client.di_refresh_token,
            "di_client_id": client.client.di_client_id,
        },
        "coordinator_data": coordinator.data,
        "probe_results": probe_data,
    }
