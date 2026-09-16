"""DataUpdateCoordinator for Garmin Jr."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
import json
import logging
import os
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_CHILD_ID,
    ATTR_CHILD_NAME,
    CONF_CHILD_PROFILE_PKS,
    CONF_NIGHT_MODE_ENABLED,
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
from .plane_spotter import prefetch_airspace_sync

FAST_MESSAGE_POLL_INTERVAL_SECONDS = 5


def resolve_child_profile_pk(
    child_id: str | int,
    child_data: dict[str, Any],
    options: dict[str, Any] | None = None,
    learned_pks: dict[str, str] | None = None,
) -> str | int | None:
    """Resolve the canonical Garmin Bounce user profile PK for a child in deterministic priority order.

    Order:
    1. Explicit manual config option override (`profile_pk_{child_id}`)
    2. Discovered `user_profile_pk` / `userProfilePk` in child payload
    3. Discovered `connect_id` / `connectId` (which maps to child profile PK in Garmin GCS messaging)
    4. Dynamically learned PK cached in `learned_pks`
    5. Fallback to `child_id`
    """
    cid = str(child_id)
    if options:
        opt_override = options.get(f"profile_pk_{cid}")
        if opt_override:
            return opt_override

    pk = (
        child_data.get("user_profile_pk")
        or child_data.get("userProfilePk")
        or child_data.get("profilePk")
        or child_data.get("userProfileId")
        or child_data.get("connect_id")
        or child_data.get("connectId")
        or (child_data.get("account") or {}).get("connectId")
    )
    if pk:
        return pk

    if learned_pks and cid in learned_pks:
        return learned_pks[cid]

    return child_data.get("child_id") or child_data.get("id") or child_id


class BoundedSet:
    """Set with an upper-bounded size backed by a rolling deque for O(1) operations."""

    def __init__(self, maxlen: int = 1000) -> None:
        self._deque: deque[str] = deque()
        self._set: set[str] = set()
        self._maxlen = maxlen

    def add(self, item: str) -> None:
        if item not in self._set:
            if len(self._deque) >= self._maxlen:
                old = self._deque.popleft()
                self._set.discard(old)
            self._deque.append(item)
            self._set.add(item)

    def __contains__(self, item: str) -> bool:
        return item in self._set

    def __len__(self) -> int:
        return len(self._set)


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

    # Strict check: school_mode MUST be explicitly enabled on the watch or options
    mode_val = school_mode.get("mode") or school_mode.get("enabled")
    is_school_enabled = False
    if isinstance(mode_val, str) and mode_val.upper() in ("RESTRICTED", "SILENT", "ALL", "ON", "TRUE"):
        is_school_enabled = True
    elif isinstance(mode_val, bool) and mode_val:
        is_school_enabled = True
    elif mode_val is None and options and options.get(CONF_SCHOOL_MODE_ENABLED) is True:
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

    # Bedtime / Wake time window
    bed_time = child_data.get("bed_time") or settings.get("bedTime") or settings.get("bed_time")
    wake_time = child_data.get("wake_time") or settings.get("wakeTime") or settings.get("wake_time")
    now_mins = current_dt.hour * 60 + current_dt.minute

    if bed_time and wake_time:
        try:
            if isinstance(bed_time, (int, float)):
                bed_mins = int(bed_time // 60) if bed_time > 1440 else int(bed_time)
            else:
                bed_h, bed_m = map(int, str(bed_time).split(":")[:2])
                bed_mins = bed_h * 60 + bed_m

            if isinstance(wake_time, (int, float)):
                wake_mins = int(wake_time // 60) if wake_time > 1440 else int(wake_time)
            else:
                wake_h, wake_m = map(int, str(wake_time).split(":")[:2])
                wake_mins = wake_h * 60 + wake_m

            if bed_mins > wake_mins:
                if now_mins >= bed_mins or now_mins < wake_mins:
                    return "sleep_time"
            else:
                if bed_mins <= now_mins < wake_mins:
                    return "sleep_time"
        except Exception:
            pass
    else:
        # Default night window when watch settings don't specify bedtime: 22:00 (10 PM) to 06:30 (6:30 AM)
        if now_mins >= 22 * 60 or now_mins < 6 * 60 + 30:
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
        self._seen_message_ids: BoundedSet = BoundedSet(maxlen=1000)
        self._initial_fetch_done: bool = False
        self._unsub_msg_poll: CALLBACK_TYPE | None = None
        self._school_mode_pause_until: float = 0.0
        self._last_night_poll_ts: float = 0.0
        self._last_unmatched_log_ts: float = 0.0
        self._school_mode_overrides: dict[str, bool] = {}
        self._was_in_school_mode: bool = False
        self._rapid_retry_unsub: CALLBACK_TYPE | None = None
        self._conversation_burst_until: float = 0.0
        self._burst_poll_unsub: CALLBACK_TYPE | None = None

        # Load persisted learned child profile PKs from config entry options
        saved_pks = entry.options.get(CONF_CHILD_PROFILE_PKS, {})
        if saved_pks and isinstance(saved_pks, dict):
            self.client._learned_child_pks.update({str(k): str(v) for k, v in saved_pks.items() if v})

        # Track last user-facing options to avoid unnecessary integration reloads on internal PK persistence
        self._last_user_options: dict[str, Any] = {
            k: v for k, v in entry.options.items() if k != CONF_CHILD_PROFILE_PKS
        }

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
        self._was_in_school_mode = False
        LOGGER.debug("Garmin Jr: School Mode polling pause reset")

    def async_start_message_polling(self) -> None:
        """Start the fast background message polling loop."""
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
        if self._rapid_retry_unsub is not None:
            self._rapid_retry_unsub()
            self._rapid_retry_unsub = None
        if self._burst_poll_unsub is not None:
            self._burst_poll_unsub()
            self._burst_poll_unsub = None

    def _async_persist_learned_pks(self) -> None:
        """Persist newly learned child profile PKs to config entry options if changed."""
        saved_learned = self.entry.options.get(CONF_CHILD_PROFILE_PKS, {})
        if self.client._learned_child_pks and self.client._learned_child_pks != saved_learned:
            new_opts = {**self.entry.options, CONF_CHILD_PROFILE_PKS: dict(self.client._learned_child_pks)}
            self.hass.config_entries.async_update_entry(self.entry, options=new_opts)
            LOGGER.debug("Persisted %d learned child profile PK(s) to config entry options", len(self.client._learned_child_pks))

    def _process_child_messages(
        self,
        child_id: str,
        child_data: dict[str, Any],
        child_messages: list[dict[str, Any]],
        now_ts: float,
        fire_events: bool = True,
    ) -> tuple[bool, bool, bool]:
        """Process messages for a child: filter seen, fire events, and update last_message attributes.

        Returns (has_updates, has_pending_transcription, has_new_child_message).
        """
        if not child_messages:
            return False, False, False

        connect_id = child_data.get("connect_id") or child_data.get("connectId") or (child_data.get("account") or {}).get("connectId")
        user_profile_pk = resolve_child_profile_pk(
            child_id,
            child_data,
            options=self.entry.options,
            learned_pks=self.client._learned_child_pks,
        )
        device_id = child_data.get("device_id") or child_data.get("deviceId")

        kid_identifiers = {
            str(child_id),
            str(connect_id or ""),
            str(device_id or ""),
            str(user_profile_pk or ""),
            str(self.client._learned_child_pks.get(str(child_id)) or ""),
        } - {"", "None", "null"}

        child_name = child_data.get(ATTR_CHILD_NAME, "Child")
        has_updates = False
        has_pending_transcription = False
        has_new_child_message = False

        for msg in reversed(child_messages):
            msg_id = str(msg.get("messageId") or "")
            if not msg_id or msg_id in self._seen_message_ids:
                continue

            text_content = (
                msg.get("messageText")
                or msg.get("text")
                or msg.get("transcription")
                or msg.get("transcript")
                or msg.get("audioTranscription")
                or (msg.get("audioMetadata") or {}).get("transcript")
                or (msg.get("audioDetails") or {}).get("transcription")
            )

            media_type = msg.get("mediaType", "")
            if not text_content and media_type in ("Audio", "audio/amr"):
                msg_time_str = msg.get("createDateTime") or msg.get("createdTimestamp") or ""
                try:
                    msg_dt = dt_util.parse_datetime(msg_time_str) if msg_time_str else None
                    if msg_dt and (dt_util.now() - msg_dt).total_seconds() < 90:
                        has_pending_transcription = True
                        continue
                except Exception:
                    pass

            if not text_content and media_type in ("Audio", "audio/amr"):
                text_content = f"[{media_type}]"

            self._seen_message_ids.add(msg_id)
            has_updates = True
            from_pk = str(msg.get("fromUserProfilePk", ""))
            is_from_child = from_pk in kid_identifiers

            if is_from_child and fire_events:
                has_new_child_message = True
                event_data = {
                    "child_id": child_id,
                    "child_name": child_name,
                    "message_id": msg_id,
                    "text": text_content,
                    "sender": msg.get("senderDisplayName") or msg.get("sender") or child_name,
                    "from_user_profile_pk": msg.get("fromUserProfilePk"),
                    "to_user_profile_pk": msg.get("toUserProfilePk"),
                    "media_type": msg.get("mediaType", "Text"),
                    "timestamp": msg.get("createDateTime") or msg.get("createdTimestamp") or msg.get("timestamp"),
                    "incoming": True,
                }
                self.hass.bus.async_fire(EVENT_MESSAGE_RECEIVED, event_data)
                LOGGER.info(
                    "Garmin Jr: Received incoming message from '%s': '%s' (fired %s)",
                    child_name,
                    text_content,
                    EVENT_MESSAGE_RECEIVED,
                )

        # Update last message attributes on child_data from newest message
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
        is_latest_from_child = from_pk in kid_identifiers
        child_data["last_message_sender"] = (
            latest_msg.get("senderDisplayName")
            or latest_msg.get("sender")
            or (child_name if is_latest_from_child else "Guardian")
        )
        child_data["last_message_media"] = latest_msg.get("mediaType", "Text")

        return has_updates, has_pending_transcription, has_new_child_message

    async def _async_poll_messages(self, _now: Any = None) -> None:
        """Adaptive poll for messages: pause until school ends in School Mode, 5m in Night Mode, fast active."""
        if not self.data or not self._initial_fetch_done:
            return

        if self._rapid_retry_unsub is not None:
            self._rapid_retry_unsub()
            self._rapid_retry_unsub = None
        if self._burst_poll_unsub is not None:
            self._burst_poll_unsub()
            self._burst_poll_unsub = None

        now_ts = time.time()
        local_now = dt_util.now()

        # 1. Check if any child is currently in an active School Mode window derived from the watch/config
        max_school_end_dt: datetime | None = None
        is_night_mode = False
        night_mode_enabled = bool(
            getattr(self.entry, "options", {}).get(CONF_NIGHT_MODE_ENABLED, False)
        ) if hasattr(self, "entry") else False

        for child_id, child_data in self.data.items():
            school_end = get_child_school_mode_end_time(child_data, local_now, getattr(self.entry, "options", {}))
            if school_end:
                if max_school_end_dt is None or school_end > max_school_end_dt:
                    max_school_end_dt = school_end
            elif night_mode_enabled and get_child_operating_mode(child_data, local_now) == "sleep_time":
                is_night_mode = True

        if max_school_end_dt and max_school_end_dt > local_now:
            pause_seconds = (max_school_end_dt - local_now).total_seconds() + 5  # 5s safety margin
            self._school_mode_pause_until = now_ts + pause_seconds
            if not self._was_in_school_mode:
                self._was_in_school_mode = True
                LOGGER.info(
                    "Garmin Jr: Watch in School Mode until %s. Pausing message polling for %d seconds",
                    max_school_end_dt.strftime("%H:%M:%S"),
                    int(pause_seconds),
                )
            return

        if self._was_in_school_mode or self._school_mode_pause_until > 0.0:
            self._was_in_school_mode = False
            self._school_mode_pause_until = 0.0
            LOGGER.info("Garmin Jr: School Mode inactive. Resuming active fast polling and requesting full refresh.")
            self.hass.async_create_task(self.async_request_refresh())

        # 2. Night Mode (Sleep Time): Relax polling to once per 5 minutes (300s) if user opted in
        if is_night_mode:
            if (now_ts - self._last_night_poll_ts) < 300:
                return
            self._last_night_poll_ts = now_ts
            LOGGER.debug("Garmin Jr: Night mode active - polling at relaxed 300s (5m) interval")
            self.hass.async_create_task(self.async_request_refresh())
            return

        # 3. Active: Poll messages
        try:
            recent_messages = await self.hass.async_add_executor_job(
                self.client.fetch_messages, None, 100
            )
            if not recent_messages:
                return

            has_updates = False
            has_pending_transcription = False
            any_messages_matched = False

            for child_id, child_data in self.data.items():
                connect_id = child_data.get("connect_id") or child_data.get("connectId") or (child_data.get("account") or {}).get("connectId")
                user_profile_pk = resolve_child_profile_pk(
                    child_id,
                    child_data,
                    options=self.entry.options,
                    learned_pks=self.client._learned_child_pks,
                )
                child_messages = self.client.parse_child_messages(
                    recent_messages,
                    kid_id=child_id,
                    connect_id=connect_id,
                    device_id=child_data.get("device_id") or child_data.get("deviceId"),
                    user_profile_pk=user_profile_pk,
                    total_family_kids=getattr(self.client, "family_kids_count", len(self.data)) or len(self.data),
                )
                if not child_messages:
                    continue

                any_messages_matched = True
                c_updates, c_pending, c_new_msg = self._process_child_messages(
                    child_id, child_data, child_messages, now_ts, fire_events=True
                )
                if c_updates:
                    has_updates = True
                if c_pending:
                    has_pending_transcription = True
                if c_new_msg:
                    self._conversation_burst_until = now_ts + 90.0

            if recent_messages and not any_messages_matched:
                LOGGER.debug("Garmin Jr: %d message(s) received from cloud, none matched registered children", len(recent_messages))
                if (now_ts - self._last_unmatched_log_ts) > 3600:
                    self._last_unmatched_log_ts = now_ts
                    sample_hashes = [
                        f"[msg {str(m.get('messageId'))[-4:] if m.get('messageId') else '***'}]"
                        for m in recent_messages[:3]
                    ]
                    LOGGER.warning(
                        "Garmin Jr: %d message(s) received from cloud did not match any registered children (%d tracked). Sample IDs: %s. Configure Profile PK overrides in Options if messages are missed.",
                        len(recent_messages),
                        len(self.data),
                        ", ".join(sample_hashes),
                    )

            if has_updates:
                self.async_set_updated_data(dict(self.data))
                self._async_persist_learned_pks()

            # Rapid retry: if an untranscribed voice note is pending in Garmin cloud,
            # retry in 2 seconds instead of waiting the full 5-second polling cycle
            if has_pending_transcription:
                if self._rapid_retry_unsub is not None:
                    self._rapid_retry_unsub()
                    self._rapid_retry_unsub = None
                LOGGER.debug("Garmin Jr: Scheduling 2-second rapid retry for pending voice transcription")
                self._rapid_retry_unsub = async_call_later(
                    self.hass, 2.0, self._async_poll_messages
                )

                # Speculatively prefetch overhead airspace for child's location in background
                for child_id, child_data in self.data.items():
                    trackpoints = child_data.get("trackpoints") or []
                    if trackpoints:
                        latest_tp = trackpoints[-1]
                        lat = latest_tp.get("latitude")
                        lon = latest_tp.get("longitude")
                        if lat and lon:
                            LOGGER.debug("Garmin Jr: Speculatively prefetching airspace for (%.4f, %.4f)", lat, lon)
                            self.hass.async_add_executor_job(prefetch_airspace_sync, lat, lon)
            elif (
                now_ts < self._conversation_burst_until
                and not is_night_mode
                and now_ts >= self._school_mode_pause_until
            ):
                # Active conversation burst: schedule next poll in 3.0 seconds
                if self._burst_poll_unsub is not None:
                    self._burst_poll_unsub()
                    self._burst_poll_unsub = None
                LOGGER.debug("Garmin Jr: Conversation burst active - scheduling next poll in 3.0s")
                self._burst_poll_unsub = async_call_later(
                    self.hass, 3.0, self._async_poll_messages
                )

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

            # If fresh data shows school mode is not active, reset school mode pause
            any_in_school = any(
                get_child_school_mode_end_time(c_data, dt_util.now(), self.entry.options) is not None
                for c_data in data.values()
            )
            if not any_in_school and (self._school_mode_pause_until > 0.0 or self._was_in_school_mode):
                self.reset_school_mode_pause()

            # Persist updated token data if changed
            current_tokens = self.client.get_token_data()
            if current_tokens and current_tokens != self.entry.data.get(CONF_TOKEN_DATA):
                new_data = {**self.entry.data, CONF_TOKEN_DATA: current_tokens}
                self.hass.config_entries.async_update_entry(self.entry, data=new_data)
                LOGGER.debug("Persisted updated Garmin session tokens to config entry")

            # Persist newly learned child profile PKs if updated
            self._async_persist_learned_pks()

            # Check for new messages and fire events
            now_ts = dt_util.now().timestamp()
            for child_id, child_data in data.items():
                new_msgs = child_data.get("new_messages", [])
                if new_msgs:
                    self._process_child_messages(
                        child_id,
                        child_data,
                        new_msgs,
                        now_ts,
                        fire_events=self._initial_fetch_done,
                    )

            self._initial_fetch_done = True
            return data

        except GarminJrAuthError as err:
            raise ConfigEntryAuthFailed(f"Garmin authentication failed: {err}") from err
        except GarminJrConnectionError as err:
            raise UpdateFailed(f"Error communicating with Garmin: {err}") from err
        except Exception as err:
            LOGGER.exception("Unexpected error fetching Garmin Jr data: %s", err)
            raise UpdateFailed(f"Unexpected error: {err}") from err

