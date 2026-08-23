"""Garmin Jr Integration for Home Assistant."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    ATTR_CHILD_ID,
    ATTR_CHILD_NAME,
    ATTR_MESSAGE,
    ATTR_TARGET,
    CONF_DI_CLIENT_ID,
    CONF_DI_REFRESH_TOKEN,
    CONF_DI_TOKEN,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_TOKEN_DATA,
    DOMAIN,
    LOGGER,
    PLATFORMS,
    SERVICE_REQUEST_LOCATION_UPDATE,
    SERVICE_SEND_MESSAGE,
)
from .coordinator import GarminJrDataUpdateCoordinator
from .garmin_client import GarminJrClient

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the Garmin Jr component services."""
    hass.data.setdefault(DOMAIN, {})

    async def async_handle_send_message(call: Any) -> None:
        """Handle send_message service call."""
        target = str(call.data.get(ATTR_TARGET, "")).strip()
        message = str(call.data.get(ATTR_MESSAGE, "")).strip()

        if not message:
            _LOGGER.warning("Garmin Jr send_message called with empty message")
            return

        for entry_id, coordinator in hass.data.get(DOMAIN, {}).items():
            if not isinstance(coordinator, GarminJrDataUpdateCoordinator) or not coordinator.data:
                continue

            target_kid_id = None
            # Match by ID or Name dynamically
            for kid_id, kid_data in coordinator.data.items():
                if not target or target.lower() in (kid_id.lower(), kid_data.get(ATTR_CHILD_NAME, "").lower()):
                    target_kid_id = kid_id
                    break

            if target_kid_id:
                success = await hass.async_add_executor_job(
                    coordinator.client.send_text_message, target_kid_id, message
                )
                if success:
                    _LOGGER.debug("Sent Garmin Jr message to %s", target_kid_id)
                    await coordinator.async_request_refresh()
                return

        _LOGGER.warning("Could not find Garmin Jr child profile matching target: %s", target)

    async def async_handle_request_location_update(call: Any) -> None:
        """Handle request_location_update service call."""
        target = str(call.data.get(ATTR_TARGET, "")).strip()

        for entry_id, coordinator in hass.data.get(DOMAIN, {}).items():
            if not isinstance(coordinator, GarminJrDataUpdateCoordinator) or not coordinator.data:
                continue

            target_kid_id = None
            for kid_id, kid_data in coordinator.data.items():
                if not target or target.lower() in (kid_id.lower(), kid_data.get(ATTR_CHILD_NAME, "").lower()):
                    target_kid_id = kid_id
                    break

            if target_kid_id:
                success = await hass.async_add_executor_job(
                    coordinator.client.request_location_update, target_kid_id
                )
                if success:
                    _LOGGER.debug("Requested location refresh for %s", target_kid_id)
                    await coordinator.async_request_refresh()
                return

        _LOGGER.warning("Could not find Garmin Jr child profile matching target: %s", target)

    hass.services.async_register(DOMAIN, SERVICE_SEND_MESSAGE, async_handle_send_message)
    hass.services.async_register(
        DOMAIN, SERVICE_REQUEST_LOCATION_UPDATE, async_handle_request_location_update
    )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Garmin Jr from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    token_data = entry.data.get(CONF_TOKEN_DATA)
    if not token_data and entry.data.get(CONF_DI_TOKEN):
        token_data = {
            "di_token": entry.data.get(CONF_DI_TOKEN),
            "di_refresh_token": entry.data.get(CONF_DI_REFRESH_TOKEN),
            "di_client_id": entry.data.get(CONF_DI_CLIENT_ID),
        }

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

