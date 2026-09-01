"""DataUpdateCoordinator for Garmin Jr."""
from __future__ import annotations

from datetime import datetime, timedelta
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
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_CHILD_ID,
    ATTR_CHILD_NAME,
    CONF_SCAN_INTERVAL,
    CONF_SCHOOL_MODE_ENABLED,
    CONF_SCHOOL_MODE_END_TIME,
    CONF_SCHOOL_MODE_START_TIME,
    CONF_TOKEN_DATA,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SCHOOL_MODE_END_TIME,
    DEFAULT_SCHOOL_MODE_START_TIME,
    DOMAIN,
    EVENT_MESSAGE_RECEIVED,
    LOGGER,
)
from .garmin_client import GarminJrAuthError, GarminJrClient, GarminJrConnectionError

FAST_MESSAGE_POLL_INTERVAL_SECONDS = 10


def format_time_str(time_val: Any) -> str:
    """Format seconds from midnight or HH:MM string to HH:MM format."""
    if isinstance(time_val, (int, float)):
        h = int(time_val // 3600)
        m = int((time_val % 3600) // 60)
        return f"{h:02d}:{m:02d}"
    return str(time_val or "")


def get_child_school_mode_end_time(
    child_data: dict[str, Any],
    current_dt: datetime | None = None,
    options: dict[str, Any] | None = None,
) -> datetime | None:
    """If child is currently in an active school mode window derived from the watch/config, return the end datetime."""
    # Check manual override for holiday / vacation
    if child_data.get("school_mode_override") is False:
        return None

    settings = child_data.get("settings") or {}
    school_mode = child_data.get("school_mode") or settings.get("schoolMode") or settings.get("school_mode") or {}

    # Strict check: school_mode MUST be explicitly enabled on the watch / options
    mode_val = school_mode.get("mode") or school_mode.get("enabled")
    is_school_enabled = False
    if isinstance(mode_val, str) and mode_val.upper() in ("RESTRICTED", "SILENT", "ALL", "ON", "TRUE"):
        is_school_enabled = True
    elif isinstance(mode_val, bool) and mode_val:
        is_school_enabled = True
    elif mode_val is None and (options is None or options.get(CONF_SCHOOL_MODE_ENABLED, True)):
        # Default enabled if not turned off
        is_school_enabled = True

    if not is_school_enabled:
        return None

    if current_dt is None:
        current_dt = dt_util.now()

    current_weekday = current_dt.weekday()  # 0-4 = Mon-Fri
    school_days = school_mode.get("days") or ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]
    day_names = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
    today_name = day_names[current_weekday]

    is_school_day = today_name in [str(d).upper() for d in school_days] or (current_weekday < 5 and not school_mode.get("days"))
    if not is_school_day:
        return None

    opt_start = options.get(CONF_SCHOOL_MODE_START_TIME) if options else None
    opt_end = options.get(CONF_SCHOOL_MODE_END_TIME) if options else None

    start_raw = school_mode.get("startTime") or school_mode.get("start_time") or opt_start or DEFAULT_SCHOOL_MODE_START_TIME
    end_raw = school_mode.get("endTime") or school_mode.get("end_time") or opt_end or DEFAULT_SCHOOL_MODE_END_TIME

    try:
        if isinstance(start_raw, (int, float)):
            start_h = int(start_raw // 3600)
            start_m = int((start_raw % 3600) // 60)
        else:
            start_h, start_m = map(int, str(start_raw).split(":")[:2])

        if isinstance(end_raw, (int, float)):
            end_h = int(end_raw // 3600)
            end_m = int((end_raw % 3600) // 60)
        else:
            end_h, end_m = map(int, str(end_raw).split(":")[:2])

        start_dt = current_dt.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
        end_dt = current_dt.replace(hour=end_h, minute=end_m, second=0, microsecond=0)

        if start_dt <= current_dt < end_dt:
            return end_dt
    except Exception as err:
        LOGGER.debug("Error parsing school mode times: %s", err)

    return None


def get_child_operating_mode(child_data: dict[str, Any], current_dt: Any = None) -> str:
    """Check if child is in School Mode, Sleep Time, or Active derived strictly from watch settings.

    Returns: 'school_mode', 'sleep_time', or 'active'.
    """
    if current_dt is None:
        current_dt = dt_util.now()

    if get_child_school_mode_end_time(child_data, current_dt) is not None:
        return "school_mode"

    settings = child_data.get("settings") or {}

    # 2. Bedtime / Wake time window
    bed_time = child_data.get("bed_time") or settings.get("bedTime") or settings.get("bed_time")
    wake_time = child_data.get("wake_time") or settings.get("wakeTime") or settings.get("wake_time")
    if bed_time and wake_time:
        try:
            if isinstance(bed_time, (int, float)):
                bed_mins = int(bed_time / 60)
            else:
                bed_h, bed_m = map(int, str(bed_time).split(":")[:2])
                bed_mins = bed_h * 60 + bed_m

            if isinstance(wake_time, (int, float)):
                wake_mins = int(wake_time / 60)
            else:
                wake_h, wake_m = map(int, str(wake_time).split(":")[:2])
                wake_mins = wake_h * 60 + wake_m

            now_mins = current_dt.hour * 60 + current_dt.minute

            if bed_mins > wake_mins:
                if now_mins >= bed_mins or now_mins < wake_mins:
                    return "sleep_time"
            else:
                if bed_mins <= now_mins < wake_mins:
                    return "sleep_time"
        except Exception:
            pass

    # 3. DND flag from watch
    if settings.get("dndEnabled") or settings.get("dnd_enabled"):
        return "sleep_time"

    return "active"


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
        self._school_mode_pause_until: float = 0.0
        self._last_night_poll_ts: float = 0.0
        self._school_mode_overrides: dict[str, bool] = {}
        self._was_in_school_mode: bool = False

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

    def set_child_school_mode_override(self, child_id: str, enabled: bool) -> None:
        """Set a manual School Mode override for a child (e.g. for holiday/vacation)."""
        self._school_mode_overrides[child_id] = enabled
        if self.data and child_id in self.data:
            self.data[child_id]["school_mode_override"] = enabled
            if not enabled and "school_mode" in self.data[child_id] and isinstance(self.data[child_id]["school_mode"], dict):
                self.data[child_id]["school_mode"]["mode"] = "Off"
                self.data[child_id]["school_mode"]["enabled"] = False
        if not enabled:
            self.reset_school_mode_pause()

    def reset_school_mode_pause(self) -> None:
        """Immediately lift any active School Mode polling pause."""
        self._school_mode_pause_until = 0.0
        LOGGER.debug("Garmin Jr: School Mode polling pause reset")

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
        """Adaptive poll for messages: pause until school ends in School Mode, 60s in Night Mode, 10s Active."""
        if not self.data or not self._initial_fetch_done:
            return

        now_ts = time.time()

        # 1. Check if we are currently paused due to active School Mode on watch
        if now_ts < self._school_mode_pause_until:
            return

        local_now = dt_util.now()

        # 2. Check if any child is currently in an active School Mode window derived from the watch/config
        max_school_end_dt: datetime | None = None
        is_night_mode = False

        for child_id, child_data in self.data.items():
            school_end = get_child_school_mode_end_time(child_data, local_now, self.entry.options)
            if school_end:
                if max_school_end_dt is None or school_end > max_school_end_dt:
                    max_school_end_dt = school_end
            elif get_child_operating_mode(child_data, local_now) == "sleep_time":
                is_night_mode = True

        if max_school_end_dt and max_school_end_dt > local_now:
            pause_seconds = (max_school_end_dt - local_now).total_seconds() + 5  # 5s safety margin
            self._school_mode_pause_until = now_ts + pause_seconds
            self._was_in_school_mode = True
            LOGGER.info(
                "Garmin Jr: Watch in School Mode until %s. Pausing message polling for %d seconds",
                max_school_end_dt.strftime("%H:%M:%S"),
                int(pause_seconds),
            )
            return

        if self._was_in_school_mode:
            self._was_in_school_mode = False
            LOGGER.info("Garmin Jr: School Mode ended for the day. Resuming active fast polling and requesting full refresh.")
            self.hass.async_create_task(self.async_request_refresh())

        # 3. Night Mode (Sleep Time): Relax polling to 60s
        if is_night_mode:
            if (now_ts - self._last_night_poll_ts) < 60:
                return
            self._last_night_poll_ts = now_ts
            LOGGER.debug("Garmin Jr: Night mode active - polling at relaxed 60s interval")

        # 4. Active: Poll messages
        try:
            recent_messages = await self.hass.async_add_executor_job(
                self.client.fetch_messages, None, 100
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

            # Apply any active manual overrides (e.g. for holiday/vacation)
            for cid, override in self._school_mode_overrides.items():
                if cid in data:
                    data[cid]["school_mode_override"] = override
                    if not override and "school_mode" in data[cid] and isinstance(data[cid]["school_mode"], dict):
                        data[cid]["school_mode"]["mode"] = "Off"
                        data[cid]["school_mode"]["enabled"] = False
                    elif override and "school_mode" in data[cid] and isinstance(data[cid]["school_mode"], dict):
                        if data[cid]["school_mode"].get("mode") == "Off":
                            data[cid]["school_mode"]["mode"] = "Restricted"
                            data[cid]["school_mode"]["enabled"] = True

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

