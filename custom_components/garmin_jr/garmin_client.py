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

    def _extract_di_token(self) -> str | None:
        """Extract DI access token from underlying client or garth instance."""
        for attr in ("di_token", "access_token", "token"):
            val = getattr(self.client, attr, None)
            if isinstance(val, str) and val:
                return val

        garth = getattr(self.client, "garth", None)
        if garth:
            oauth2 = getattr(garth, "oauth2_token", None)
            if oauth2:
                for attr in ("access_token", "token"):
                    val = getattr(oauth2, attr, None)
                    if isinstance(val, str) and val:
                        return val
                if isinstance(oauth2, dict) and oauth2.get("access_token"):
                    return oauth2["access_token"]

        try:
            raw = self.client.dumps()
            if raw:
                try:
                    td = json.loads(raw)
                    if isinstance(td, dict):
                        return td.get("di_token") or td.get("access_token") or td.get("token")
                except Exception:
                    pass
        except Exception:
            pass

        try:
            hdrs = self.client.get_api_headers()
            auth = hdrs.get("Authorization", "")
            if auth.startswith("Bearer "):
                return auth[7:].strip()
        except Exception:
            pass

        return None

    def _ensure_it_token(self) -> None:
        """Ensure a valid IT token exists, exchanging DI token or refreshing if needed."""
        now = time.time()
        if self._it_token and self._it_expires_at and (now + 300) < self._it_expires_at:
            return

        # 1. Try refresh token if available
        if self._it_refresh_token:
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
                    return
                else:
                    _LOGGER.debug("Garmin IT token refresh failed: %s %s", resp.status_code, resp.text)
            except Exception as err:
                _LOGGER.debug("Exception during IT token refresh: %s", err)

        # 2. Exchange DI token for Garmin Jr token via diauth
        di_token = self._extract_di_token()
        if di_token:
            try:
                url = "https://diauth.garmin.com/di-oauth2-service/oauth/token"
                data = {
                    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "subject_token": di_token,
                    "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                    "client_id": "VIVOFIT_JR_ANDROID",
                }
                resp = requests.post(url, data=data, timeout=15)
                if resp.status_code == 200:
                    res_data = resp.json()
                    self._it_token = res_data.get("access_token")
                    self._it_refresh_token = res_data.get("refresh_token", self._it_refresh_token)
                    expires_in = res_data.get("expires_in", 21600)
                    self._it_expires_at = now + expires_in
                    _LOGGER.debug("Exchanged DI token for Garmin Jr OAuth2 token successfully")
                    return
                else:
                    _LOGGER.warning("diauth token exchange returned %s: %s", resp.status_code, resp.text)
            except Exception as err:
                _LOGGER.warning("Exception during diauth token exchange: %s", err)

    def _get_it_headers(self) -> dict[str, str]:
        """Get headers for Garmin LTE / GCS API endpoints."""
        self._ensure_it_token()
        token = self._it_token or self._extract_di_token() or ""
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

            headers = self._get_it_headers()
            resp = requests.get(
                f"{VIVOKID_BASE_URL}/v3/family/info", headers=headers, timeout=10
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

    def fetch_geofences(self, kid_profile_id: str | int | None = None) -> list[dict[str, Any]]:
        """Fetch all configured Garmin Safe Zones / Geofences from Vivokid API."""
        try:
            headers = self._get_it_headers()
            url = f"{VIVOKID_BASE_URL}/geofence/all"
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    geofences: list[dict[str, Any]] = []
                    for g in data:
                        geofences.append({
                            "id": g.get("geofenceId") or g.get("id"),
                            "name": g.get("name"),
                            "latitude": g.get("latitude"),
                            "longitude": g.get("longitude"),
                            "radius": g.get("radius"),
                            "wifi_ssid": g.get("wifiSsid"),
                            "kid_ids": g.get("kidIds", []),
                        })
                    return geofences
        except Exception as err:
            _LOGGER.debug("Could not fetch Garmin geofences: %s", err)
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
        """Fetch all child devices, steps, location, messages, geofences, and telemetry."""
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
        headers = self._get_it_headers()
        today = datetime.date.today().isoformat()

        # 1. Fetch Garmin Jr Family Info
        family_id = self._family_id
        family_name = self._family_name
        kids_list: list[dict[str, Any]] = []

        try:
            fam_resp = requests.get(f"{VIVOKID_BASE_URL}/v3/family/info", headers=headers, timeout=10)
            if fam_resp.status_code == 200:
                fam_data = fam_resp.json()
                families = fam_data.get("families", [])
                if families:
                    family = families[0]
                    family_id = family.get("familyId")
                    family_name = family.get("name")
                    kids_list = family.get("kids", [])
                elif fam_data.get("family"):
                    family = fam_data.get("family", {})
                    family_id = family.get("familyId")
                    family_name = family.get("name")
                    kids_list = family.get("kids", [])
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

        # 3. Fetch Garmin Safe Zones / Geofences across the family
        all_geofences: list[dict[str, Any]] = []
        geofence_by_id: dict[str, dict[str, Any]] = {}
        try:
            all_geofences = self.fetch_geofences()
            geofence_by_id = {str(g["id"]): g for g in all_geofences if g.get("id") is not None}
        except Exception as gf_err:
            _LOGGER.debug("Error fetching Garmin geofences: %s", gf_err)

        # 4. Fetch Kids from Garmin Jr Leaderboard & Activity Summaries
        if family_id:
            try:
                # Merge kids from family info and leaderboard
                kids_map: dict[str, dict[str, Any]] = {}
                for k in kids_list:
                    k_id = str(k.get("id"))
                    kids_map[k_id] = {
                        "id": k_id,
                        "displayName": k.get("name") or "Child",
                        "deviceId": k.get("deviceId"),
                        "hasLteDevice": k.get("hasLteDevice", True),
                        "totalPoints": k.get("totalPoints"),
                    }

                lb_url = f"{VIVOKID_BASE_URL}/v2/leaderboard/daily/{family_id}/{today}"
                lb_resp = requests.get(lb_url, headers=headers, timeout=10)
                if lb_resp.status_code == 200:
                    for kid in lb_resp.json().get("kidStepsData", []):
                        k_id = str(kid.get("id"))
                        if k_id in kids_map:
                            kids_map[k_id].update(kid)
                        else:
                            kids_map[k_id] = kid

                for kid_id, kid in kids_map.items():
                    kid_name = kid.get("displayName") or kid.get("name") or "Child"
                    live_steps = kid.get("steps") or 0
                    last_sync = kid.get("lastSyncDate")

                    # Filter geofences applicable to this kid
                    kid_int = int(kid_id) if kid_id.isdigit() else None
                    kid_geofences = [
                        g for g in all_geofences
                        if not g.get("kid_ids") or (kid_int is not None and kid_int in g.get("kid_ids", []))
                    ]

                    # Fetch Daily Step Summary
                    step_goal = 7500
                    steps_record = None
                    active_mins = None
                    try:
                        sum_url = f"{VIVOKID_BASE_URL}/v2/activity/summary/kid/{kid_id}/{today}"
                        sum_resp = requests.get(sum_url, headers=headers, timeout=10)
                        if sum_resp.status_code == 200:
                            sum_data = sum_resp.json()
                            step_goal = sum_data.get("stepsGoal") or step_goal
                            steps_record = sum_data.get("stepsRecord")
                            active_mins = (
                                sum_data.get("activeMinutes")
                                or sum_data.get("activeMinute")
                                or ((sum_data.get("walkingMinutes") or 0) + (sum_data.get("runningMinutes") or 0))
                            )
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
                        pr_resp = requests.get(pr_url, headers=headers, timeout=10)
                        if pr_resp.status_code == 200:
                            pr_data = pr_resp.json()
                            steps_record = pr_data.get("stepsRecord") or steps_record
                            active_mins_record = pr_data.get("activeMinuteRecord")
                    except Exception as pr_err:
                        _LOGGER.debug("Could not fetch kid personal records for %s: %s", kid_id, pr_err)

                    # Query Device Info & Battery
                    battery_level = None
                    battery_status = "NORMAL"
                    try:
                        dinfo = self.client.connectapi(f"/wellness-service/wellness/deviceInfo/{kid_id}")
                        if isinstance(dinfo, dict):
                            if dinfo.get("batteryLevel") is not None:
                                battery_level = dinfo.get("batteryLevel")
                            if dinfo.get("batteryStatus"):
                                battery_status = str(dinfo.get("batteryStatus")).upper()
                            if dinfo.get("lastSyncDate") and not last_sync:
                                last_sync = datetime.datetime.fromtimestamp(
                                    dinfo.get("lastSyncDate") / 1000.0, tz=datetime.timezone.utc
                                ).isoformat()
                    except Exception as d_err:
                        _LOGGER.debug("Could not query device info for kid %s: %s", kid_id, d_err)

                    # Fetch Live GPS Trackpoints & Geofence Status
                    latitude = None
                    longitude = None
                    gps_accuracy = 15
                    location_ts = None
                    fix_type = None
                    active_geofence_id = None
                    active_geofence_name = None
                    has_wifi = False
                    geofence_lat = None
                    geofence_lon = None
                    geofence_radius = None

                    trackpoints = self.fetch_trackpoints(kid_id, limit=1)
                    if trackpoints:
                        latest_pt = trackpoints[0]
                        pos = latest_pt.get("position") or {}
                        latitude = latest_pt.get("latitude") if latest_pt.get("latitude") is not None else pos.get("latitude")
                        longitude = latest_pt.get("longitude") if latest_pt.get("longitude") is not None else pos.get("longitude")
                        gps_accuracy = latest_pt.get("accuracy") or latest_pt.get("accuracyMeters") or latest_pt.get("horizontalAccuracy") or 15
                        location_ts = latest_pt.get("timestamp") or latest_pt.get("dateTime") or latest_pt.get("date")
                        fix_type = latest_pt.get("fixType")

                        # Check for active geofence state
                        fp_data = latest_pt.get("familyPointData") or {}
                        status_changes = fp_data.get("statusChanges") or []
                        for sc in status_changes:
                            dev_state = sc.get("deviceState")
                            g_id = sc.get("geofenceId")
                            if dev_state == "GeofenceEnter" or (g_id and dev_state != "GeofenceExit"):
                                active_geofence_id = str(g_id)
                                gf = geofence_by_id.get(active_geofence_id)
                                if gf:
                                    active_geofence_name = gf.get("name")
                                    has_wifi = bool(gf.get("wifi_ssid"))
                                    geofence_lat = gf.get("latitude")
                                    geofence_lon = gf.get("longitude")
                                    geofence_radius = gf.get("radius")
                                break

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
                        "battery_level": battery_level,
                        "battery_status": battery_status,
                        "steps": live_steps,
                        "daily_step_goal": step_goal,
                        "steps_record": steps_record,
                        "active_minutes": active_mins,
                        "active_minutes_record": active_mins_record,
                        "last_sync": last_sync or time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "family_id": family_id,
                        "family_name": family_name,
                        "last_message": last_msg_text,
                        "last_message_time": last_msg_time,
                        "last_message_sender": last_msg_sender,
                        "last_message_media": last_msg_media,
                        "garmin_safe_zone": active_geofence_name,
                        "garmin_geofence_id": active_geofence_id,
                        "fix_type": fix_type,
                        "has_wifi": has_wifi,
                        "geofence_latitude": geofence_lat,
                        "geofence_longitude": geofence_lon,
                        "geofence_radius": geofence_radius,
                        "geofences": kid_geofences,
                        "new_messages": recent_messages,
                    }
            except Exception as lb_err:
                _LOGGER.warning("Error fetching kid leaderboard for family %s: %s", family_id, lb_err)

        # 5. Discover Adult / Registered Devices (Garmin Connect device registry)
        try:
            raw_devices = self.client.connectapi("/device-service/deviceregistration/devices")
            if isinstance(raw_devices, list):
                for dev in raw_devices:
                    dev_id = str(dev.get("deviceId") or dev.get("deviceNumber") or dev.get("id") or "unknown")
                    dev_name = dev.get("displayName") or dev.get("productDisplayName") or "Garmin Watch"
                    model = dev.get("partNumber") or dev.get("productDisplayName") or "Garmin Device"

                    child_name = dev.get("assignedName") or dev.get("displayName") or f"Device ({dev_id[-4:]})"
                    child_id = str(dev.get("userId") or dev.get("childId") or dev_id)

                    dev_battery_level = dev.get("batteryLevel")
                    dev_battery_status = dev.get("batteryStatus", "NORMAL")
                    if dev_battery_level is None:
                        try:
                            dinfo = self.client.connectapi(f"/wellness-service/wellness/deviceInfo/{dev_id}")
                            if isinstance(dinfo, dict):
                                if dinfo.get("batteryLevel") is not None:
                                    dev_battery_level = dinfo.get("batteryLevel")
                                if dinfo.get("batteryStatus"):
                                    dev_battery_status = str(dinfo.get("batteryStatus")).upper()
                        except Exception:
                            pass

                    if child_id in results:
                        if dev_battery_level is not None:
                            results[child_id]["battery_level"] = dev_battery_level
                        if dev_battery_status:
                            results[child_id]["battery_status"] = dev_battery_status
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
                        "battery_level": dev_battery_level,
                        "battery_status": dev_battery_status,
                        "steps": 0,
                        "daily_step_goal": 10000,
                        "steps_record": None,
                        "active_minutes": None,
                        "active_minutes_record": None,
                        "last_sync": dev.get("lastSyncTime") or time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "family_id": family_id,
                        "family_name": family_name,
                        "last_message": None,
                        "last_message_time": None,
                        "last_message_sender": None,
                        "last_message_media": None,
                        "garmin_safe_zone": None,
                        "garmin_geofence_id": None,
                        "fix_type": None,
                        "has_wifi": False,
                        "geofences": [],
                        "new_messages": [],
                    }
        except Exception as dev_err:
            _LOGGER.warning("Could not fetch Garmin Connect device list: %s", dev_err)

        return results

