"""Garmin Jr API Client for Home Assistant."""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from garminconnect.client import Client
from garminconnect.exceptions import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

_LOGGER = logging.getLogger(__name__)


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
        self._user_guid: str | None = None
        self._display_name: str | None = None
        self._user_id: str | None = None

        if token_data:
            self._load_token_data(token_data)

    def _load_token_data(self, token_data: str | dict[str, Any]) -> None:
        """Load session tokens into the underlying client."""
        try:
            if isinstance(token_data, dict):
                token_str = json.dumps(token_data)
            else:
                token_str = str(token_data)
            self.client.loads(token_str)
            _LOGGER.debug("Loaded Garmin tokens into client")
        except Exception as err:
            _LOGGER.error("Failed to load Garmin tokens: %s", err)
            raise GarminJrAuthError(f"Invalid token format: {err}") from err

    def get_token_data(self) -> dict[str, Any]:
        """Dump current session tokens as a dictionary."""
        try:
            return json.loads(self.client.dumps())
        except Exception:
            return {
                "di_token": self.client.di_token,
                "di_refresh_token": self.client.di_refresh_token,
                "di_client_id": self.client.di_client_id,
            }

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

            devices = self.client.connectapi("/device-service/deviceregistration/devices")
            return isinstance(devices, list)
        except Exception as err:
            _LOGGER.debug("Session validation failed: %s", err)
            return False

    def _query_url(self, url: str) -> tuple[int, Any]:
        """Query a direct URL using current auth headers."""
        try:
            headers = self.client.get_api_headers()
            resp = self.client._api_session.get(url, headers=headers, timeout=5)
            if resp.status_code == 200:
                try:
                    return 200, resp.json()
                except Exception:
                    return 200, resp.text
            return resp.status_code, None
        except Exception as e:
            return 0, str(e)

    def _probe_endpoints(self) -> dict[str, Any]:
        """Deep probe candidate Garmin Family, Child, and LiveTrack endpoints across domains."""
        discovered: dict[str, Any] = {}

        try:
            profile = self.client.connectapi("/userprofile-service/socialProfile")
            if isinstance(profile, dict):
                self._display_name = profile.get("displayName")
                self._user_guid = profile.get("garminGUID")
                self._user_id = str(profile.get("profileId") or profile.get("id") or "")
        except Exception:
            pass

        d_name = self._display_name or ""
        u_guid = self._user_guid or ""
        u_id = self._user_id or ""

        probe_paths = [
            "/family-service/family",
            f"/family-service/family/{u_id}" if u_id else "",
            f"/family-service/family/user/{d_name}" if d_name else "",
            f"/family-service/family/user/{u_guid}" if u_guid else "",
            "/family-service/family/members",
            "/family-service/family/children",
            "/family-service/family/summary",
            "/family-service/user/family",
            "/child-operations/family",
            f"/child-operations/family/{d_name}" if d_name else "",
            "/child-operations/children",
            "/child-service/family",
            "/child-service/children",
            "/child-summary-service/family",
            "/kids-service/family",
            "/kids-service/children",
            "/junior-service/family",
            "/vivofit-jr/family",
            "/parental-service/family",
            "/safety-service/family",
            "/geofence-service/geofences",
            "/lte-service/devices",
            "/livetrack-service/livetrack/session",
            "/livetrack-service/livetrack/contacts",
            "/livetrack-service/livetrack/tokens",
            "/livetrack-service/livetrack/settings",
            "/userprofile-service/socialProfile/connections",
        ]

        domains = [
            "https://connectapi.garmin.com",
            "https://services.garmin.com",
            "https://mobile.garmin.com",
            "https://livetrack.garmin.com",
        ]

        found_log: list[str] = []

        for domain in domains:
            for path in probe_paths:
                if not path:
                    continue
                url = f"{domain}/{path.lstrip('/')}"
                status, res = self._query_url(url)
                if status == 200:
                    discovered[url] = res
                    found_log.append(f"200:{url}")
                    _LOGGER.warning("GARMIN_PROBE_200: [%s] -> %s", url, str(res)[:300])
                elif status in (400, 401, 403):
                    found_log.append(f"{status}:{url}")

        _LOGGER.warning("GARMIN_MULTI_DOMAIN_SUMMARY: %s", ", ".join(found_log[:15]))
        return discovered

    def fetch_all_data(self) -> dict[str, dict[str, Any]]:
        """Fetch all child devices, steps, battery, and location data."""
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

        # 1. Probe family and child endpoints
        discovered = self._probe_endpoints()

        # 2. Discover Registered Devices
        devices: list[dict[str, Any]] = []
        try:
            raw_devices = self.client.connectapi("/device-service/deviceregistration/devices")
            if isinstance(raw_devices, list):
                devices = raw_devices
        except Exception as err:
            _LOGGER.warning("Could not fetch device list: %s", err)

        # 3. Parse Children from Discovered Family Endpoints
        for url, data in discovered.items():
            if isinstance(data, dict):
                children_list = (
                    data.get("children")
                    or data.get("familyMembers")
                    or data.get("members")
                    or data.get("kids")
                )
                if isinstance(children_list, list):
                    for child in children_list:
                        if not isinstance(child, dict):
                            continue
                        child_id = str(child.get("childId") or child.get("id") or child.get("userId") or f"child_{len(results)}")
                        child_name = child.get("displayName") or child.get("name") or child.get("firstName") or "Benjamin"
                        device_info = child.get("device") or (child.get("devices", [{}])[0] if isinstance(child.get("devices"), list) and child.get("devices") else {})
                        dev_id = str(device_info.get("deviceId") or child.get("deviceId") or child_id)
                        model = device_info.get("productDisplayName") or device_info.get("partNumber") or child.get("deviceModel") or "Garmin Bounce 2"

                        lat = None
                        lon = None
                        accuracy = 15
                        loc_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ")

                        for loc_ep in (
                            f"/location-service/device/{dev_id}",
                            f"/device-service/device/{dev_id}/location",
                            f"/family-service/family/child/{child_id}/location",
                            f"/child-operations/child/{child_id}/location",
                        ):
                            try:
                                loc_data = self.client.connectapi(loc_ep)
                                if isinstance(loc_data, dict) and ("latitude" in loc_data or "lat" in loc_data):
                                    lat = loc_data.get("latitude") or loc_data.get("lat")
                                    lon = loc_data.get("longitude") or loc_data.get("lon") or loc_data.get("lng")
                                    accuracy = loc_data.get("accuracy") or loc_data.get("horizontalAccuracy") or 15
                                    loc_ts = loc_data.get("timestamp") or loc_data.get("lastUpdated") or loc_ts
                                    break
                            except Exception:
                                pass

                        steps = child.get("totalSteps") or child.get("steps") or 0
                        active_mins = child.get("activeMinutes") or 0
                        battery = child.get("batteryLevel") or device_info.get("batteryLevel")

                        results[child_id] = {
                            "child_id": child_id,
                            "child_name": child_name,
                            "device_id": dev_id,
                            "device_name": f"{child_name}'s {model}",
                            "model": model,
                            "latitude": lat,
                            "longitude": lon,
                            "gps_accuracy": accuracy,
                            "location_timestamp": loc_ts,
                            "battery_level": battery,
                            "battery_status": device_info.get("batteryStatus", "NORMAL"),
                            "steps": steps,
                            "daily_step_goal": child.get("stepGoal", 6000),
                            "active_minutes": active_mins,
                            "last_sync": child.get("lastSyncTime") or time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        }

        # 4. Standard Devices Fallback
        for dev in devices:
            dev_id = str(dev.get("deviceId") or dev.get("deviceNumber") or dev.get("id") or "unknown")
            dev_name = dev.get("displayName") or dev.get("productDisplayName") or "Garmin Watch"
            model = dev.get("partNumber") or dev.get("productDisplayName") or "Garmin Device"

            child_name = dev.get("assignedName") or dev.get("displayName") or f"Device ({dev_id[-4:]})"
            child_id = str(dev.get("userId") or dev.get("childId") or dev_id)

            if child_id in results:
                continue

            battery_level = dev.get("batteryLevel")
            battery_status = dev.get("batteryStatus", "NORMAL")

            lat = None
            lon = None
            accuracy = 15
            loc_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ")
            steps = 0
            active_mins = 0

            # Check location
            try:
                loc_data = self.client.connectapi(f"/device-service/device/{dev_id}/location")
                if isinstance(loc_data, dict) and ("latitude" in loc_data or "lat" in loc_data):
                    lat = loc_data.get("latitude") or loc_data.get("lat")
                    lon = loc_data.get("longitude") or loc_data.get("lon") or loc_data.get("lng")
                    accuracy = loc_data.get("accuracy") or loc_data.get("horizontalAccuracy") or 15
                    loc_ts = loc_data.get("timestamp") or loc_data.get("lastUpdated") or loc_ts
            except Exception:
                pass

            try:
                today_str = time.strftime("%Y-%m-%d")
                stats = self.client.connectapi(f"/usersummary-service/usersummary/daily/{dev_id}?calendarDate={today_str}")
                if isinstance(stats, dict):
                    steps = stats.get("totalSteps") or stats.get("steps") or 0
                    active_mins = stats.get("activeMinutes") or stats.get("moderateIntensityMinutes") or 0
                    if battery_level is None:
                        battery_level = stats.get("batteryLevel")
            except Exception:
                pass

            results[child_id] = {
                "child_id": child_id,
                "child_name": child_name,
                "device_id": dev_id,
                "device_name": dev_name,
                "model": model,
                "latitude": lat,
                "longitude": lon,
                "gps_accuracy": accuracy,
                "location_timestamp": loc_ts,
                "battery_level": battery_level,
                "battery_status": battery_status,
                "steps": steps,
                "daily_step_goal": dev.get("stepGoal", 6000),
                "active_minutes": active_mins,
                "last_sync": dev.get("lastSyncTime") or time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }

        return results
