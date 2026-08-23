"""Garmin Jr API Client for Home Assistant."""
from __future__ import annotations

import datetime
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

VIVOKID_BASE_URL = "https://vivokidapi.garmin.com/GCSVivokidServlet"


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

    def fetch_all_data(self) -> dict[str, dict[str, Any]]:
        """Fetch all child devices, steps, battery, and telemetry from Garmin Jr and Connect."""
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

        # 2. Fetch Kids from Garmin Jr Leaderboard & Activity Summaries
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

                        results[kid_id] = {
                            "child_id": kid_id,
                            "child_name": kid_name,
                            "device_id": kid_id,
                            "device_name": f"{kid_name}'s Bounce",
                            "model": "Garmin Bounce",
                            "latitude": None,
                            "longitude": None,
                            "gps_accuracy": 15,
                            "location_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "battery_level": None,
                            "battery_status": "NORMAL",
                            "steps": live_steps,
                            "daily_step_goal": step_goal,
                            "steps_record": steps_record,
                            "active_minutes_record": active_mins_record,
                            "last_sync": last_sync or time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "family_id": family_id,
                            "family_name": family_name,
                        }
            except Exception as lb_err:
                _LOGGER.warning("Error fetching kid leaderboard for family %s: %s", family_id, lb_err)

        # 3. Discover Adult Registered Devices (Venu, etc.)
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
                    }
        except Exception as dev_err:
            _LOGGER.warning("Could not fetch Garmin Connect device list: %s", dev_err)

        return results
