"""DataUpdateCoordinator for Garmin Jr."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_SCAN_INTERVAL,
    CONF_TOKEN_DATA,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
)
from .garmin_client import GarminJrAuthError, GarminJrClient, GarminJrConnectionError


class GarminJrDataUpdateCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Class to manage fetching Garmin Jr data from the API."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: GarminJrClient,
    ) -> None:
        """Initialize the coordinator."""
        self.client = client
        self.entry = entry

        scan_interval_seconds = entry.options.get(
            CONF_SCAN_INTERVAL,
            entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )

        super().__init__(
            hass,
            LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(seconds=scan_interval_seconds),
        )

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Fetch data from Garmin API in executor."""
        try:
            data = await self.hass.async_add_executor_job(self.client.fetch_all_data)

            # Persist updated token data if changed
            current_tokens = self.client.get_token_data()
            if current_tokens and current_tokens != self.entry.data.get(CONF_TOKEN_DATA):
                new_data = {**self.entry.data, CONF_TOKEN_DATA: current_tokens}
                self.hass.config_entries.async_update_entry(self.entry, data=new_data)
                LOGGER.debug("Persisted updated Garmin session tokens to config entry")

            return data

        except GarminJrAuthError as err:
            LOGGER.error("Garmin Jr authentication error during update: %s", err)
            raise ConfigEntryAuthFailed(err) from err
        except GarminJrConnectionError as err:
            LOGGER.warning("Garmin Jr connection warning: %s", err)
            raise UpdateFailed(f"Connection error: {err}") from err
        except Exception as err:
            LOGGER.exception("Unexpected error fetching Garmin Jr data: %s", err)
            raise UpdateFailed(f"Unexpected error: {err}") from err
