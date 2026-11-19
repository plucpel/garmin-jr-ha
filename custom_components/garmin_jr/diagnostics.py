"""Diagnostics support for Garmin Jr."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CHILD_PROFILE_PKS,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_TOKEN_DATA,
    CONF_ZONE_MAPPING,
    DOMAIN,
)
from .coordinator import GarminJrDataUpdateCoordinator

TO_REDACT = {
    CONF_PASSWORD,
    CONF_TOKEN_DATA,
    CONF_EMAIL,
    CONF_CHILD_PROFILE_PKS,
    CONF_ZONE_MAPPING,
    "email",
    "di_token",
    "di_refresh_token",
    "di_client_id",
    "it_token",
    "it_refresh_token",
    "tokens",
    "latitude",
    "longitude",
    "geofence_latitude",
    "geofence_longitude",
    "wifi_ssid",
    "wifiSsid",
    "last_message",
    "new_messages",
    "messageText",
    "text",
    "transcription",
    "transcript",
    "senderDisplayName",
    "sender",
    "child_id",
    "id",
    "connect_id",
    "connectId",
    "user_profile_pk",
    "userProfilePk",
    "device_id",
    "deviceId",
    "family_id",
    "family_name",
    "child_name",
    "displayName",
    "name",
    "account",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for Garmin Jr config entry with PII and credentials redacted."""
    coordinator: GarminJrDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    anonymized_coordinator_data = {
        f"child_{idx}": cdata
        for idx, (_, cdata) in enumerate((coordinator.data or {}).items(), start=1)
    }

    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "entry_options": async_redact_data(dict(entry.options), TO_REDACT),
        "coordinator_data": async_redact_data(anonymized_coordinator_data, TO_REDACT),
    }
