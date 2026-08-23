"""Diagnostics support for Garmin Jr."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_PASSWORD, CONF_TOKEN_DATA, DOMAIN
from .coordinator import GarminJrDataUpdateCoordinator

TO_REDACT = {
    CONF_PASSWORD,
    CONF_TOKEN_DATA,
    "di_token",
    "di_refresh_token",
    "di_client_id",
    "tokens",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for Garmin Jr config entry."""
    coordinator: GarminJrDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "coordinator_data": async_redact_data(dict(coordinator.data or {}), TO_REDACT),
    }
