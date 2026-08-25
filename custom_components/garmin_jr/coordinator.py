"""DataUpdateCoordinator for Garmin Jr."""
from __future__ import annotations

from datetime import timedelta
import json
import logging
import os
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ATTR_CHILD_ID,
    ATTR_CHILD_NAME,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN_DATA,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_MESSAGE_RECEIVED,
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
        self._seen_message_ids: set[str] = set()
        self._initial_fetch_done: bool = False

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

            # Check for new messages and fire events
            for child_id, child_data in data.items():
                new_msgs = child_data.get("new_messages", [])
                for msg in new_msgs:
                    msg_id = str(msg.get("messageId") or "")
                    if not msg_id:
                        continue

                    if msg_id not in self._seen_message_ids:
                        self._seen_message_ids.add(msg_id)
                        # Only fire event after initial startup load to avoid replay storms
                        if self._initial_fetch_done:
                            text_content = (
                                msg.get("messageText")
                                or msg.get("text")
                                or msg.get("transcript")
                                or msg.get("transcription")
                                or msg.get("audioTranscription")
                                or (msg.get("audioMetadata") or {}).get("transcript")
                                or (msg.get("audioDetails") or {}).get("transcription")
                                or (f"[{msg.get('mediaType', 'Audio')}]" if msg.get("mediaType") == "Audio" else "")
                            )
                            event_data = {
                                "child_id": child_id,
                                "child_name": child_data.get(ATTR_CHILD_NAME, "Child"),
                                "message_id": msg_id,
                                "text": text_content,
                                "sender": msg.get("senderDisplayName") or msg.get("sender"),
                                "media_type": msg.get("mediaType", "Text"),
                                "timestamp": msg.get("createdTimestamp"),
                            }
                            self.hass.bus.async_fire(EVENT_MESSAGE_RECEIVED, event_data)
                            LOGGER.debug("Fired %s event for message %s (text: %s)", EVENT_MESSAGE_RECEIVED, msg_id, text_content)

            self._initial_fetch_done = True
            return data

        except GarminJrAuthError as err:
            raise ConfigEntryAuthFailed(f"Garmin authentication failed: {err}") from err
        except GarminJrConnectionError as err:
            raise UpdateFailed(f"Error communicating with Garmin: {err}") from err
        except Exception as err:
            LOGGER.exception("Unexpected error fetching Garmin Jr data: %s", err)
            raise UpdateFailed(f"Unexpected error: {err}") from err

