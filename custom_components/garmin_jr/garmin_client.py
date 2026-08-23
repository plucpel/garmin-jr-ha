"""Garmin Jr API Client for Home Assistant."""
from __future__ import annotations

import base64
import contextlib
import datetime
import json
import logging
import time
from typing import Any
import uuid

from garminconnect.client import Client
from garminconnect.exceptions import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)
import requests

_LOGGER = logging.getLogger(__name__)

VIVOKID_BASE_URL = "https://vivokidapi.garmin.com/GCSVivokidServlet"
GCS_API_BASE_URL = "https://api.gcs.garmin.com"
SERVICES_BASE_URL = "https://services.garmin.com"


class GarminJrAuthError(Exception):
    """Exception raised for authentication failures."""


class GarminJrConnectionError(Exception):
    """Exception raised for network or connection failures."""


class GarminJrClient:
    """Client to interact with Garmin Jr and Garmin Connect APIs."""

    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        token_data: str | dict[str, Any] | None = None,
    ) -> None:
        """Initialize the client."""
        self.email = email
        self.password = password
        self._raw_token_data = token_data
        self.client = Client()
        self._family_id: int | None = None
        self._family_name: str | None = None
        self._it_token: str | None = None
        self._it_refresh_token: str | None = None
        self._it_expires_at: float | None = None
        self._last_seen_message_ids: set[str] = set()

        if token_data:
            self._load_token_data(token_data)

    def _load_token_data(self, token_data: str | dict[str, Any]) -> None:
        """Load session tokens into the underlying client and extract IT tokens."""
        try:
            if isinstance(token_data, str):
                try:
                    data = json.loads(token_data)
                except Exception:
                    data = {}
                token_str = token_data
            else:
                data = token_data
                token_str = json.dumps(token_data)

            self.client.loads(token_str)
            self._it_token = data.get("it_token")
            self._it_refresh_token = data.get("it_refresh_token")
            self._it_expires_at = data.get("it_expires_at")
            _LOGGER.debug("Loaded Garmin tokens into client (has IT token: %s)", bool(self._it_token))
        except Exception as err:
            _LOGGER.error("Failed to load Garmin tokens: %s", err)
            raise GarminJrAuthError(f"Invalid token format: {err}") from err

    def get_token_data(self) -> dict[str, Any]:
        """Dump current session tokens as a dictionary."""
        base_tokens: dict[str, Any] = {}
        try:
            base_tokens = json.loads(self.client.dumps())
        except Exception:
            base_tokens = {
                "di_token": getattr(self.client, "di_token", None),
                "di_refresh_token": getattr(self.client, "di_refresh_token", None),
                "di_client_id": getattr(self.client, "di_client_id", None),
            }

        if self._it_token:
            base_tokens["it_token"] = self._it_token
        if self._it_refresh_token:
            base_tokens["it_refresh_token"] = self._it_refresh_token
        if self._it_expires_at:
            base_tokens["it_expires_at"] = self._it_expires_at

        return base_tokens

    def _refresh_it_token_if_needed(self) -> None:
        """Refresh the IT token if expired or close to expiry."""
        now = time.time()
        if self._it_expires_at and (now + 300) < self._it_expires_at and self._it_token:
            return

        if not self._it_refresh_token:
            return

        try:
            url = f"{SERVICES_BASE_URL}/api/oauth/token"
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "GarminJr/5.23.0 (Android)",
            }
            data = {
                "grant_type": "refresh_token",
                "client_id": "VIVOFIT_JR_ANDROID",
                "refresh_token": self._it_refresh_token,
            }
            resp = requests.post(url, headers=headers, data=data, timeout=15)
            if resp.status_code == 200:
                res_data = resp.json()
                self._it_token = res_data.get("access_token")
                self._it_refresh_token = res_data.get("refresh_token", self._it_refresh_token)
                expires_in = res_data.get("expires_in", 3600)
                self._it_expires_at = now + expires_in
                _LOGGER.debug("Refreshed Garmin IT OAuth2 token successfully")
            else:
                _LOGGER.warning("Garmin IT token refresh failed: %s %s", resp.status_code, resp.text)
        except Exception as err:
            _LOGGER.warning("Exception during IT token refresh: %s", err)

    def _get_it_headers(self) -> dict[str, str]:
        """Get headers for Garmin LTE / GCS API endpoints."""
        self._refresh_it_token_if_needed()
        token = self._it_token or getattr(self.client, "di_token", "")
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": "GarminJr/5.23.0 (Android)",
            "Accept": "application/json",
            "X-garmin-client-id": "VIVOFIT_JR_ANDROID",
        }
        return headers

    def login_sync(self, prompt_mfa: Any = None) -> tuple[str | None, Any]:
        """Perform synchronous login using Garmin credentials."""
        if not self.email or not self.password:
            raise GarminJrAuthError("Email and password required for login")

        try:
            status, data = self.client.login(
                email=self.email,
                password=self.password,
                prompt_mfa=prompt_mfa,
                return_on_mfa=(prompt_mfa is None),
            )
            return status, data
        except GarminConnectAuthenticationError as err:
            raise GarminJrAuthError(f"Authentication failed: {err}") from err
        except GarminConnectTooManyRequestsError as err:
            raise GarminJrConnectionError(f"Rate limited by Garmin: {err}") from err
        except Exception as err:
            raise GarminJrConnectionError(f"Login failed: {err}") from err

    def resume_mfa_sync(self, mfa_code: str) -> None:
        """Complete MFA login."""
        try:
            self.client.resume_login(None, mfa_code)
        except Exception as err:
            raise GarminJrAuthError(f"MFA verification failed: {err}") from err

    def validate_session(self) -> bool:
        """Validate if the current session or tokens are functional."""
        try:
            if self.client._token_expires_soon():
                self.client._refresh_session()

            headers = self.client.get_api_headers()
            resp = self.client._api_session.get(
                f"{VIVOKID_BASE_URL}/v2/family/info", headers=headers, timeout=10
            )
            if resp.status_code == 200:
                return True

            devices = self.client.connectapi("/device-service/deviceregistration/devices")
            return isinstance(devices, list)
        except Exception as err:
            _LOGGER.debug("Session validation failed: %s", err)
            return False

    def fetch_trackpoints(self, kid_profile_id: str | int, limit: int = 1) -> list[dict[str, Any]]:
        """Fetch latest GPS trackpoints for a child profile from GCS API."""
        try:
            headers = self._get_it_headers()
            url = f"{GCS_API_BASE_URL}/tracker/family/api/v1/trackpoints"
            params = {"kidProfileId": str(kid_profile_id), "limit": limit}
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
                if isinstance(data, dict):
                    return data.get("trackPoints") or data.get("points") or []
        except Exception as err:
            _LOGGER.debug("Could not fetch trackpoints for kid %s: %s", kid_profile_id, err)
        return []

    def fetch_messages(self, after_iso: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Fetch message history from GCS Messaging API."""
        try:
            headers = self._get_it_headers()
            url = f"{GCS_API_BASE_URL}/messaging/family/api/v1/guardian/messages"
            params: dict[str, Any] = {"limit": limit, "audioMediaType": "OPUS"}
            if after_iso:
                params["after"] = after_iso
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
                if isinstance(data, dict):
                    return data.get("messages") or []
        except Exception as err:
            _LOGGER.debug("Could not fetch messages from GCS API: %s", err)
        return []

    def send_text_message(self, to_user_profile_pk: str | int, message_text: str) -> bool:
        """Send a text message to a child's Garmin Bounce watch."""
        try:
            headers = self._get_it_headers()
            headers["Content-Type"] = "application/json"
            url = f"{GCS_API_BASE_URL}/messaging/family/api/v1/messages/user/text"
            payload = {
                "toUserProfilePk": int(to_user_profile_pk),
                "mediaType": "Text",
                "messageText": message_text,
                "sendRequestId": str(uuid.uuid4()),
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=15)
            if resp.status_code in (200, 201, 204):
                _LOGGER.debug("Successfully sent text message to profile %s", to_user_profile_pk)
                return True
            _LOGGER.warning("Failed to send message to %s: %s %s", to_user_profile_pk, resp.status_code, resp.text)
        except Exception as err:
            _LOGGER.error("Error sending text message to %s: %s", to_user_profile_pk, err)
        return False

    def request_location_update(self, device_or_kid_id: str | int) -> bool:
        """Ping the child's watch over LTE to request an immediate GPS location refresh."""
        try:
            headers = self._get_it_headers()
            url = f"{GCS_API_BASE_URL}/device-instruction/api/v1/family/{device_or_kid_id}/update-location"
            resp = requests.post(url, headers=headers, timeout=15)
            if resp.status_code in (200, 201, 202, 204):
                _LOGGER.debug("Location refresh instruction dispatched to %s", device_or_kid_id)
                return True
            _LOGGER.warning("Failed to dispatch location refresh for %s: %s %s", device_or_kid_id, resp.status_code, resp.text)
        except Exception as err:
            _LOGGER.error("Error requesting location update for %s: %s", device_or_kid_id, err)
        return False

    def fetch_all_data(self) -> dict[str, dict[str, Any]]:
        """Fetch all child devices, steps, location, messages, and telemetry."""
        if not self.client.is_authenticated:
            if self._raw_token_data:
                self._load_token_data(self._raw_token_data)
            elif self.email and self.password:
                self.login_sync()
            else:
                raise GarminJrAuthError("Client is not authenticated")

        if self.client._token_expires_soon():
            try:
                self.client._refresh_session()
            except Exception as err:
                _LOGGER.warning("Token refresh warning: %s", err)

        results: dict[str, dict[str, Any]] = {}
        headers = self.client.get_api_headers()
        sess = self.client._api_session
        today = datetime.date.today().isoformat()

        # 1. Fetch Garmin Jr Family Info
        family_id = self._family_id
        family_name = self._family_name

        try:
            fam_resp = sess.get(f"{VIVOKID_BASE_URL}/v2/family/info", headers=headers, timeout=10)
            if fam_resp.status_code == 200:
                fam_data = fam_resp.json()
                family = fam_data.get("family", {})
                family_id = family.get("familyId")
                family_name = family.get("name")
                self._family_id = family_id
                self._family_name = family_name
                _LOGGER.debug("Discovered Garmin Jr Family: %s (ID: %s)", family_name, family_id)
        except Exception as err:
            _LOGGER.warning("Error querying Garmin Jr family info: %s", err)

        # 2. Fetch Recent Messages across the family
        recent_messages: list[dict[str, Any]] = []
        try:
            recent_messages = self.fetch_messages(limit=30)
        except Exception as msg_err:
            _LOGGER.debug("Error fetching recent messages: %s", msg_err)

        # 3. Fetch Kids from Garmin Jr Leaderboard & Activity Summaries
        if family_id:
            try:
                lb_url = f"{VIVOKID_BASE_URL}/v2/leaderboard/daily/{family_id}/{today}"
                lb_resp = sess.get(lb_url, headers=headers, timeout=10)
                if lb_resp.status_code == 200:
                    kids_data = lb_resp.json().get("kidStepsData", [])
                    for kid in kids_data:
                        kid_id = str(kid.get("id"))
                        kid_name = kid.get("displayName") or "Child"
                        live_steps = kid.get("steps") or 0
                        last_sync = kid.get("lastSyncDate")

                        # Fetch Daily Step Summary
                        step_goal = 7500
                        steps_record = None
                        try:
                            sum_url = f"{VIVOKID_BASE_URL}/v2/activity/summary/kid/{kid_id}/{today}"
                            sum_resp = sess.get(sum_url, headers=headers, timeout=10)
                            if sum_resp.status_code == 200:
                                sum_data = sum_resp.json()
                                step_goal = sum_data.get("stepsGoal") or step_goal
                                steps_record = sum_data.get("stepsRecord")
                                if sum_data.get("lastSyncDate"):
                                    ts_ms = sum_data.get("lastSyncDate")
                                    last_sync = datetime.datetime.fromtimestamp(
                                        ts_ms / 1000.0, tz=datetime.timezone.utc
                                    ).isoformat()
                        except Exception as sum_err:
                            _LOGGER.debug("Could not fetch kid summary for %s: %s", kid_id, sum_err)

                        # Fetch Personal Records
                        active_mins_record = None
                        try:
                            pr_url = f"{VIVOKID_BASE_URL}/v2/activity/personalrecords/{kid_id}"
                            pr_resp = sess.get(pr_url, headers=headers, timeout=10)
                            if pr_resp.status_code == 200:
                                pr_data = pr_resp.json()
                                steps_record = pr_data.get("stepsRecord") or steps_record
                                active_mins_record = pr_data.get("activeMinuteRecord")
                        except Exception as pr_err:
                            _LOGGER.debug("Could not fetch kid personal records for %s: %s", kid_id, pr_err)

                        # Fetch Live GPS Trackpoints
                        latitude = None
                        longitude = None
                        gps_accuracy = 15
                        location_ts = None
                        trackpoints = self.fetch_trackpoints(kid_id, limit=1)
                        if trackpoints:
                            latest_pt = trackpoints[0]
                            latitude = latest_pt.get("latitude")
                            longitude = latest_pt.get("longitude")
                            gps_accuracy = latest_pt.get("accuracy") or latest_pt.get("horizontalAccuracy") or 15
                            location_ts = latest_pt.get("timestamp") or latest_pt.get("date")

                        # Match latest message for this child
                        last_msg_text = None
                        last_msg_time = None
                        last_msg_sender = None
                        last_msg_media = None
                        for msg in recent_messages:
                            to_pk = str(msg.get("toUserProfilePk", ""))
                            from_pk = str(msg.get("fromUserProfilePk", ""))
                            if kid_id in (to_pk, from_pk):
                                last_msg_text = msg.get("messageText") or msg.get("text") or msg.get("mediaType", "Message")
                                last_msg_time = msg.get("createdTimestamp") or msg.get("timestamp")
                                last_msg_sender = msg.get("senderDisplayName") or msg.get("sender") or ("Child" if from_pk == kid_id else "Guardian")
                                last_msg_media = msg.get("mediaType", "Text")
                                break

                        results[kid_id] = {
                            "child_id": kid_id,
                            "child_name": kid_name,
                            "device_id": kid_id,
                            "device_name": f"{kid_name}'s Bounce",
                            "model": "Garmin Bounce",
                            "latitude": latitude,
                            "longitude": longitude,
                            "gps_accuracy": gps_accuracy,
                            "location_timestamp": location_ts or time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "battery_level": None,
                            "battery_status": "NORMAL",
                            "steps": live_steps,
                            "daily_step_goal": step_goal,
                            "steps_record": steps_record,
                            "active_minutes_record": active_mins_record,
                            "last_sync": last_sync or time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "family_id": family_id,
                            "family_name": family_name,
                            "last_message": last_msg_text,
                            "last_message_time": last_msg_time,
                            "last_message_sender": last_msg_sender,
                            "last_message_media": last_msg_media,
                            "new_messages": recent_messages,
                        }
            except Exception as lb_err:
                _LOGGER.warning("Error fetching kid leaderboard for family %s: %s", family_id, lb_err)

        # 4. Discover Adult / Registered Devices (Garmin Connect device registry)
        try:
            raw_devices = self.client.connectapi("/device-service/deviceregistration/devices")
            if isinstance(raw_devices, list):
                for dev in raw_devices:
                    dev_id = str(dev.get("deviceId") or dev.get("deviceNumber") or dev.get("id") or "unknown")
                    dev_name = dev.get("displayName") or dev.get("productDisplayName") or "Garmin Watch"
                    model = dev.get("partNumber") or dev.get("productDisplayName") or "Garmin Device"

                    child_name = dev.get("assignedName") or dev.get("displayName") or f"Device ({dev_id[-4:]})"
                    child_id = str(dev.get("userId") or dev.get("childId") or dev_id)

                    if child_id in results:
                        if dev.get("batteryLevel") is not None:
                            results[child_id]["battery_level"] = dev.get("batteryLevel")
                        if dev.get("batteryStatus"):
                            results[child_id]["battery_status"] = dev.get("batteryStatus")
                        continue

                    results[child_id] = {
                        "child_id": child_id,
                        "child_name": child_name,
                        "device_id": dev_id,
                        "device_name": dev_name,
                        "model": model,
                        "latitude": None,
                        "longitude": None,
                        "gps_accuracy": 15,
                        "location_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "battery_level": dev.get("batteryLevel"),
                        "battery_status": dev.get("batteryStatus", "NORMAL"),
                        "steps": 0,
                        "daily_step_goal": 10000,
                        "steps_record": None,
                        "active_minutes_record": None,
                        "last_sync": dev.get("lastSyncTime") or time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "family_id": family_id,
                        "family_name": family_name,
                        "last_message": None,
                        "last_message_time": None,
                        "last_message_sender": None,
                        "last_message_media": None,
                        "new_messages": [],
                    }
        except Exception as dev_err:
            _LOGGER.warning("Could not fetch Garmin Connect device list: %s", dev_err)

        return results
