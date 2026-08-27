"""DataUpdateCoordinator for Garmin Jr."""
from __future__ import annotations

from datetime import timedelta
import json
import logging
import os
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_time_interval
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

FAST_MESSAGE_POLL_INTERVAL_SECONDS = 10


def is_child_in_quiet_time(child_data: dict[str, Any], current_dt: Any = None) -> tuple[bool, str]:
    """Check if child is in School Mode, DND, or Sleep Time based strictly on watch settings."""
    import datetime
    if current_dt is None:
        current_dt = datetime.datetime.now()

    settings = child_data.get("settings") or {}

    # 1. School Mode check
    school_mode = child_data.get("school_mode") or settings.get("schoolMode") or settings.get("school_mode") or {}
    if school_mode.get("enabled", True):
        current_weekday = current_dt.weekday()  # 0-4 = Mon-Fri
        school_days = school_mode.get("days") or ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]

        start_str = school_mode.get("startTime") or school_mode.get("start_time") or "08:00"
        end_str = school_mode.get("endTime") or school_mode.get("end_time") or "15:30"

        try:
            start_h, start_m = map(int, str(start_str).split(":"))
            end_h, end_m = map(int, str(end_str).split(":"))
            start_minutes = start_h * 60 + start_m
            end_minutes = end_h * 60 + end_m
            now_minutes = current_dt.hour * 60 + current_dt.minute

            day_names = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
            today_name = day_names[current_weekday]

            is_school_day = today_name in [str(d).upper() for d in school_days] or (current_weekday < 5 and not school_mode.get("days"))
            if is_school_day and start_minutes <= now_minutes < end_minutes:
                return True, "school_mode"
        except Exception:
            pass

    # 2. Bedtime / Wake time window
    bed_time_str = child_data.get("bed_time") or settings.get("bedTime") or settings.get("bed_time") or "21:00"
    wake_time_str = child_data.get("wake_time") or settings.get("wakeTime") or settings.get("wake_time") or "07:00"
    try:
        bed_h, bed_m = map(int, str(bed_time_str).split(":"))
        wake_h, wake_m = map(int, str(wake_time_str).split(":"))
        bed_mins = bed_h * 60 + bed_m
        wake_mins = wake_h * 60 + wake_m
        now_mins = current_dt.hour * 60 + current_dt.minute

        if bed_mins > wake_mins:
            if now_mins >= bed_mins or now_mins < wake_mins:
                return True, "sleep_time"
        else:
            if bed_mins <= now_mins < wake_mins:
                return True, "sleep_time"
    except Exception:
        pass

    # 3. DND flag
    if settings.get("dndEnabled") or settings.get("dnd_enabled"):
        return True, "dnd"

    return False, "active"


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
        self._unsub_msg_poll: CALLBACK_TYPE | None = None
        self._last_quiet_poll_ts: float = 0.0

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

    def async_start_message_polling(self) -> None:
        """Start the fast 10-second background message polling loop."""
        if self._unsub_msg_poll is None:
            self._unsub_msg_poll = async_track_time_interval(
                self.hass,
                self._async_poll_messages,
                timedelta(seconds=FAST_MESSAGE_POLL_INTERVAL_SECONDS),
            )
            LOGGER.debug("Started Garmin Jr fast message polling timer (%ss)", FAST_MESSAGE_POLL_INTERVAL_SECONDS)

    def async_unload(self) -> None:
        """Unsubscribe all background listeners on coordinator unload."""
        if self._unsub_msg_poll is not None:
            self._unsub_msg_poll()
            self._unsub_msg_poll = None
            LOGGER.debug("Stopped Garmin Jr fast message polling timer")

    async def _async_poll_messages(self, _now: Any = None) -> None:
        """Adaptive poll for incoming messages: fast 10s active, quiet 15m in School/Night."""
        if not self.data or not self._initial_fetch_done:
            return

        # Check if child is in quiet mode (School Mode, DND, Bedtime) based strictly on watch settings
        all_quiet = True
        quiet_reason = ""
        for child_id, child_data in self.data.items():
            is_quiet, reason = is_child_in_quiet_time(child_data)
            if not is_quiet:
                all_quiet = False
                break
            else:
                quiet_reason = reason

        if all_quiet and self.data:
            now_ts = time.time()
            if (now_ts - self._last_quiet_poll_ts) < 900:
                return
            self._last_quiet_poll_ts = now_ts
            LOGGER.debug("Garmin Jr quiet mode active (%s) - running relaxed 15-min background poll", quiet_reason)

        try:
            recent_messages = await self.hass.async_add_executor_job(
                self.client.fetch_messages, None, 15
            )
            if not recent_messages:
                return

            has_updates = False
            for child_id, child_data in self.data.items():
                connect_id = child_data.get("connectId") or child_data.get("account", {}).get("connectId")
                child_messages = self.client.parse_child_messages(
                    recent_messages,
                    kid_id=child_id,
                    connect_id=connect_id,
                    device_id=child_data.get("device_id"),
                )
                if not child_messages:
                    continue

                for msg in child_messages:
                    msg_id = str(msg.get("messageId") or "")
                    if not msg_id or msg_id in self._seen_message_ids:
                        continue

                    self._seen_message_ids.add(msg_id)
                    has_updates = True

                    text_content = (
                        msg.get("messageText")
                        or msg.get("text")
                        or msg.get("transcription")
                        or msg.get("transcript")
                        or msg.get("audioTranscription")
                        or (msg.get("audioMetadata") or {}).get("transcript")
                        or (msg.get("audioDetails") or {}).get("transcription")
                        or (f"[{msg.get('mediaType', 'Audio')}]" if msg.get("mediaType") in ("Audio", "audio/amr") else "")
                    )
                    from_pk = str(msg.get("fromUserProfilePk", ""))
                    kid_identifiers = {
                        str(child_id),
                        str(connect_id or ""),
                        str(child_data.get("device_id") or ""),
                        "137662175",
                    } - {"", "None", "null"}

                    # Only fire EVENT_MESSAGE_RECEIVED for incoming messages sent by the child
                    is_from_child = from_pk in kid_identifiers
                    if is_from_child:
                        event_data = {
                            "child_id": child_id,
                            "child_name": child_data.get(ATTR_CHILD_NAME, "Child"),
                            "message_id": msg_id,
                            "text": text_content,
                            "sender": msg.get("senderDisplayName") or msg.get("sender") or ("Benjamin" if is_from_child else "Guardian"),
                            "from_user_profile_pk": msg.get("fromUserProfilePk"),
                            "to_user_profile_pk": msg.get("toUserProfilePk"),
                            "media_type": msg.get("mediaType", "Text"),
                            "timestamp": msg.get("createDateTime") or msg.get("createdTimestamp") or msg.get("timestamp"),
                            "incoming": is_from_child,
                        }
                        self.hass.bus.async_fire(EVENT_MESSAGE_RECEIVED, event_data)
                        LOGGER.info("Fast message poll: received incoming Garmin message from child '%s' (event %s fired)", text_content, EVENT_MESSAGE_RECEIVED)

                # Update the latest message attributes on child_data
                latest_msg = child_messages[0]
                child_data["last_message"] = (
                    latest_msg.get("messageText")
                    or latest_msg.get("text")
                    or latest_msg.get("transcription")
                    or latest_msg.get("transcript")
                    or latest_msg.get("audioTranscription")
                    or (latest_msg.get("audioMetadata") or {}).get("transcript")
                    or (latest_msg.get("audioDetails") or {}).get("transcription")
                    or (f"[{latest_msg.get('mediaType', 'Audio')}]" if latest_msg.get("mediaType") in ("Audio", "audio/amr") else None)
                )
                child_data["last_message_time"] = latest_msg.get("createDateTime") or latest_msg.get("createdTimestamp") or latest_msg.get("timestamp")
                from_pk = str(latest_msg.get("fromUserProfilePk", ""))
                kid_identifiers = {
                    str(child_id),
                    str(connect_id or ""),
                    str(child_data.get("device_id") or ""),
                    "137662175",
                } - {"", "None", "null"}
                child_data["last_message_sender"] = (
                    latest_msg.get("senderDisplayName")
                    or latest_msg.get("sender")
                    or ("Child" if from_pk in kid_identifiers else "Guardian")
                )
                child_data["last_message_media"] = latest_msg.get("mediaType", "Text")

            if has_updates:
                self.async_set_updated_data(dict(self.data))

        except Exception as err:
            LOGGER.debug("Error during fast message poll: %s", err)

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
                                or msg.get("transcription")
                                or msg.get("transcript")
                                or msg.get("audioTranscription")
                                or (msg.get("audioMetadata") or {}).get("transcript")
                                or (msg.get("audioDetails") or {}).get("transcription")
                                or (f"[{msg.get('mediaType', 'Audio')}]" if msg.get("mediaType") in ("Audio", "audio/amr") else "")
                            )
                            from_pk = str(msg.get("fromUserProfilePk", ""))
                            kid_identifiers = {
                                str(child_id),
                                str(child_data.get("connectId") or ""),
                                str(child_data.get("device_id") or ""),
                                "137662175",
                            } - {"", "None", "null"}
                            is_from_child = from_pk in kid_identifiers
                            if is_from_child:
                                event_data = {
                                    "child_id": child_id,
                                    "child_name": child_data.get(ATTR_CHILD_NAME, "Child"),
                                    "message_id": msg_id,
                                    "text": text_content,
                                    "sender": msg.get("senderDisplayName") or msg.get("sender") or ("Benjamin" if is_from_child else "Guardian"),
                                    "from_user_profile_pk": msg.get("fromUserProfilePk"),
                                    "to_user_profile_pk": msg.get("toUserProfilePk"),
                                    "media_type": msg.get("mediaType", "Text"),
                                    "timestamp": msg.get("createDateTime") or msg.get("createdTimestamp") or msg.get("timestamp"),
                                    "incoming": is_from_child,
                                }
                                self.hass.bus.async_fire(EVENT_MESSAGE_RECEIVED, event_data)
                                LOGGER.debug("Fired %s event for incoming message %s (text: %s)", EVENT_MESSAGE_RECEIVED, msg_id, text_content)

            self._initial_fetch_done = True
            return data

        except GarminJrAuthError as err:
            raise ConfigEntryAuthFailed(f"Garmin authentication failed: {err}") from err
        except GarminJrConnectionError as err:
            raise UpdateFailed(f"Error communicating with Garmin: {err}") from err
        except Exception as err:
            LOGGER.exception("Unexpected error fetching Garmin Jr data: %s", err)
            raise UpdateFailed(f"Unexpected error: {err}") from err

