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

            # Attempt a lightweight request to verify token
            devices = self.client.connectapi("/device-service/deviceregistration/devices")
            return isinstance(devices, list)
        except Exception as err:
            _LOGGER.debug("Session validation failed: %s", err)
            return False

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

        # 1. Discover Registered Devices
        devices: list[dict[str, Any]] = []
        try:
            raw_devices = self.client.connectapi("/device-service/deviceregistration/devices")
            if isinstance(raw_devices, list):
                devices = raw_devices
        except Exception as err:
            _LOGGER.warning("Could not fetch device list: %s", err)

        # 2. Try fetching family / children profiles
        family_members: list[dict[str, Any]] = []
        for endpoint in (
            "/family-service/family",
            "/child-service/family",
            "/child-summary/family",
            "/userprofile-service/userprofile/personal-information",
        ):
            try:
                data = self.client.connectapi(endpoint)
                if isinstance(data, dict):
                    members = data.get("members") or data.get("children") or data.get("familyMembers")
                    if isinstance(members, list):
                        family_members = members
                        break
            except Exception:
                continue

        # 3. Match devices and compile child entries
        if not devices and not family_members:
            # Fallback mock/sample device structure if none returned to prevent crashes
            _LOGGER.debug("No devices or family members found on account")
            return {}

        # Parse devices into child records
        for dev in devices:
            dev_id = str(dev.get("deviceId") or dev.get("deviceNumber") or dev.get("id") or "unknown")
            dev_name = dev.get("displayName") or dev.get("productDisplayName") or "Garmin Watch"
            model = dev.get("partNumber") or dev.get("productDisplayName") or "Garmin Device"

            # Check if this is a Bounce or Junior device or standard wearable
            child_name = dev.get("assignedName") or dev.get("displayName") or f"Child ({dev_id[-4:]})"
            child_id = str(dev.get("userId") or dev.get("childId") or dev_id)

            # Battery extraction
            battery_level = dev.get("batteryLevel")
            battery_status = dev.get("batteryStatus", "NORMAL")

            # Try to fetch device-specific telemetry or location if supported
            lat = None
            lon = None
            accuracy = None
            loc_ts = None
            steps = None
            active_mins = None

            # Attempt live tracking / last location lookup for Bounce LTE
            try:
                loc_data = self.client.connectapi(f"/device-service/device/{dev_id}/location")
                if isinstance(loc_data, dict):
                    lat = loc_data.get("latitude") or loc_data.get("lat")
                    lon = loc_data.get("longitude") or loc_data.get("lon") or loc_data.get("lng")
                    accuracy = loc_data.get("accuracy") or loc_data.get("horizontalAccuracy")
                    loc_ts = loc_data.get("timestamp") or loc_data.get("lastUpdated")
            except Exception:
                pass

            # Summary stats (steps, active minutes)
            try:
                today_str = time.strftime("%Y-%m-%d")
                stats = self.client.connectapi(f"/usersummary-service/usersummary/daily/{dev_id}?calendarDate={today_str}")
                if isinstance(stats, dict):
                    steps = stats.get("totalSteps") or stats.get("steps")
                    active_mins = stats.get("activeMinutes") or stats.get("moderateIntensityMinutes")
                    if not battery_level:
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
                "gps_accuracy": accuracy or 10,
                "location_timestamp": loc_ts or time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "battery_level": battery_level,
                "battery_status": battery_status,
                "steps": steps or 0,
                "daily_step_goal": dev.get("stepGoal", 6000),
                "active_minutes": active_mins or 0,
                "last_sync": dev.get("lastSyncTime") or time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }

        return results
