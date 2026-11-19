"""Garmin Jr API Client for Home Assistant."""
from __future__ import annotations

import base64
from collections import deque
import contextlib
import datetime
import json
import logging
import math
import threading
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
from requests.adapters import HTTPAdapter

try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    cffi_requests = None  # type: ignore[assignment]
    HAS_CURL_CFFI = False

_LOGGER = logging.getLogger(__name__)

VIVOKID_BASE_URL = "https://vivokidapi.garmin.com/GCSVivokidServlet"
GCS_API_BASE_URL = "https://api.gcs.garmin.com"
SERVICES_BASE_URL = "https://services.garmin.com"


class GarminJrAuthError(Exception):
    """Exception raised for authentication failures."""


class GarminJrConnectionError(Exception):
    """Exception raised for network or connection failures."""


def _haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in meters between two WGS84 coordinates."""
    r = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def extract_message_text(msg: dict[str, Any], include_audio_placeholder: bool = False) -> str | None:
    """Extract normalized text or audio transcription from a Garmin GCS message payload."""
    text = (
        msg.get("messageText")
        or msg.get("text")
        or msg.get("transcription")
        or msg.get("transcript")
        or msg.get("audioTranscription")
        or (msg.get("audioMetadata") or {}).get("transcript")
        or (msg.get("audioDetails") or {}).get("transcription")
    )
    if text:
        return str(text)
    if include_audio_placeholder and msg.get("mediaType") in ("Audio", "audio/amr"):
        return f"[{msg.get('mediaType', 'Audio')}]"
    return None


def resolve_trackpoints_and_safe_zone(
    trackpoints: list[dict[str, Any]],
    kid_geofences: list[dict[str, Any]],
    prev_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve latest GPS coordinates, accuracy, battery, and active geofence from trackpoints."""
    prev = prev_record or {}
    geofence_by_id = {str(g["id"]): g for g in (kid_geofences or []) if g.get("id") is not None}

    result: dict[str, Any] = {
        "latitude": prev.get("latitude"),
        "longitude": prev.get("longitude"),
        "gps_accuracy": prev.get("gps_accuracy", 15),
        "location_timestamp": prev.get("location_timestamp"),
        "fix_type": prev.get("fix_type"),
        "garmin_geofence_id": prev.get("garmin_geofence_id"),
        "garmin_safe_zone": prev.get("garmin_safe_zone"),
        "has_wifi": prev.get("has_wifi", False),
        "geofence_latitude": prev.get("geofence_latitude"),
        "geofence_longitude": prev.get("geofence_longitude"),
        "geofence_radius": prev.get("geofence_radius"),
        "tp_battery": None,
        "tp_charging": False,
    }

    if not trackpoints:
        return result

    sorted_tps = sorted(
        trackpoints,
        key=lambda tp: str(tp.get("timestamp") or tp.get("dateTime") or tp.get("date") or ""),
    )
    latest_pt = sorted_tps[-1]
    pos = latest_pt.get("position") or {}

    raw_lat = pos.get("lat")
    raw_lon = pos.get("lon")
    if raw_lat is not None and raw_lon is not None:
        latitude = raw_lat * (180.0 / 2147483648.0)
        longitude = raw_lon * (180.0 / 2147483648.0)
    else:
        latitude = latest_pt.get("latitude") or pos.get("latitude")
        longitude = latest_pt.get("longitude") or pos.get("longitude")

    gps_accuracy = latest_pt.get("accuracy") or latest_pt.get("accuracyMeters") or 15
    location_ts = latest_pt.get("timestamp") or latest_pt.get("dateTime") or latest_pt.get("date")
    fix_type = latest_pt.get("fixType")

    result["latitude"] = latitude
    result["longitude"] = longitude
    result["gps_accuracy"] = gps_accuracy
    result["location_timestamp"] = location_ts
    result["fix_type"] = fix_type
    result["tp_battery"] = latest_pt.get("batteryLevel")
    result["tp_charging"] = latest_pt.get("isBatteryCharging") is True

    active_geofence_id = None
    active_geofence_name = None
    has_wifi = False
    geofence_lat = None
    geofence_lon = None
    geofence_radius = None

    # Walk trackpoints newest-first; within a single trackpoint prioritize GEOFENCEENTER over GEOFENCEEXIT
    # so overlapping geofence transitions don't misclassify entry as "Outside".
    for tp in reversed(sorted_tps):
        fp_data = tp.get("familyPointData") or {}
        status_changes = fp_data.get("statusChanges") or []
        entered_ids: list[str] = []
        exited = False
        for sc in status_changes:
            dev_state = str(sc.get("deviceState") or "").upper().replace("_", "")
            g_id = sc.get("geofenceId")
            if dev_state == "GEOFENCEENTER" and g_id is not None:
                entered_ids.append(str(g_id))
            elif dev_state == "GEOFENCEEXIT":
                exited = True

        if entered_ids:
            active_geofence_id = entered_ids[-1]
            gf = geofence_by_id.get(active_geofence_id)
            if gf:
                active_geofence_name = gf.get("name")
                has_wifi = bool(gf.get("wifi_ssid"))
                geofence_lat = gf.get("latitude")
                geofence_lon = gf.get("longitude")
                geofence_radius = gf.get("radius")
            else:
                active_geofence_name = f"Zone {active_geofence_id}"
            break
        if exited:
            active_geofence_name = "Outside"
            break

    # Fallback 1: Match current GPS coordinates against configured geofence radii
    if active_geofence_name is None and latitude is not None and longitude is not None and kid_geofences:
        best_gf = None
        best_dist = float("inf")
        acc_margin = min(float(gps_accuracy or 15), 75.0)
        for gf in kid_geofences:
            gf_lat = gf.get("latitude")
            gf_lon = gf.get("longitude")
            if gf_lat is None or gf_lon is None:
                continue
            try:
                dist_m = _haversine_distance_m(float(latitude), float(longitude), float(gf_lat), float(gf_lon))
                radius_m = float(gf.get("radius") or 150.0)
                if dist_m <= (radius_m + acc_margin) and dist_m < best_dist:
                    best_dist = dist_m
                    best_gf = gf
            except Exception:
                continue
        if best_gf is not None:
            active_geofence_id = str(best_gf.get("id")) if best_gf.get("id") is not None else None
            active_geofence_name = best_gf.get("name") or (
                f"Zone {active_geofence_id}" if active_geofence_id else None
            )
            has_wifi = bool(best_gf.get("wifi_ssid"))
            geofence_lat = best_gf.get("latitude")
            geofence_lon = best_gf.get("longitude")
            geofence_radius = best_gf.get("radius")

    # Fallback 2: Preserve previous safe zone when no GeofenceExit occurred, unless a high-accuracy
    # GPS fix (>= 500m beyond zone radius) proves the watch moved far away.
    prev_zone_name = prev.get("garmin_safe_zone")
    if active_geofence_name is None and prev_zone_name and prev_zone_name != "Outside":
        moved_far_away = False
        try:
            acc_val = float(gps_accuracy or 999)
        except Exception:
            acc_val = 999.0

        prev_gf_lat = prev.get("geofence_latitude")
        prev_gf_lon = prev.get("geofence_longitude")
        prev_gf_rad = prev.get("geofence_radius")
        if (prev_gf_lat is None or prev_gf_lon is None) and kid_geofences:
            prev_gf_id = str(prev.get("garmin_geofence_id") or "")
            matched_gf = geofence_by_id.get(prev_gf_id) or next(
                (g for g in kid_geofences if str(g.get("name") or "").lower() == str(prev_zone_name).lower()),
                None,
            )
            if matched_gf:
                prev_gf_lat = matched_gf.get("latitude")
                prev_gf_lon = matched_gf.get("longitude")
                prev_gf_rad = matched_gf.get("radius")

        if (
            latitude is not None
            and longitude is not None
            and acc_val <= 50.0
            and prev_gf_lat is not None
            and prev_gf_lon is not None
        ):
            try:
                dist_from_prev = _haversine_distance_m(
                    float(latitude), float(longitude), float(prev_gf_lat), float(prev_gf_lon)
                )
                prev_radius_m = float(prev_gf_rad or 150.0)
                if dist_from_prev > (max(prev_radius_m, 150.0) + 500.0):
                    moved_far_away = True
            except Exception:
                pass

        if not moved_far_away:
            active_geofence_id = prev.get("garmin_geofence_id")
            active_geofence_name = prev_zone_name
            has_wifi = prev.get("has_wifi", False)
            geofence_lat = prev.get("geofence_latitude")
            geofence_lon = prev.get("geofence_longitude")
            geofence_radius = prev.get("geofence_radius")

    if active_geofence_name is None:
        active_geofence_name = "Outside"

    result["garmin_geofence_id"] = active_geofence_id
    result["garmin_safe_zone"] = active_geofence_name
    result["has_wifi"] = has_wifi
    result["geofence_latitude"] = geofence_lat
    result["geofence_longitude"] = geofence_lon
    result["geofence_radius"] = geofence_radius
    return result


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
        self._diauth_token: str | None = None
        self._diauth_expires_at: float | None = None
        self._it_token: str | None = None
        self._it_refresh_token: str | None = None
        self._it_expires_at: float | None = None
        self._sent_message_texts: deque[tuple[float, str]] = deque(maxlen=30)
        self._kid_diauth_tokens: dict[str, dict[str, Any]] = {}
        self._learned_child_pks: dict[str, str] = {}
        self._guardian_profile_pks: set[str] = set()
        self.family_kids_count: int = 0
        self._lock = threading.RLock()
        self._auth_backoff_until: float = 0.0
        self._http_session: Any = None
        self._gcs_rate_limited_until: float = 0.0
        self._last_rate_limit_log_ts: float = 0.0
        self._known_kids: dict[str, dict[str, Any]] = {}
        self._last_results: dict[str, dict[str, Any]] = {}
        self._send_retry_delays: tuple[float, ...] = (1.5, 3.0)
        self.last_messages_fetch_ok: bool = False

        if token_data:
            self._load_token_data(token_data)

    def _create_http_session(self) -> Any:
        """Create a persistent Android-emulated HTTP session with TLS/connection pooling and cookie retention."""
        if HAS_CURL_CFFI and cffi_requests is not None:
            try:
                browser_type = getattr(cffi_requests, "BrowserType", None)
                for target in ("chrome131_android", "chrome99_android", "chrome"):
                    if browser_type is None or hasattr(browser_type, target):
                        return cffi_requests.Session(impersonate=target, default_headers=False)
            except Exception as err:
                _LOGGER.debug("Could not initialize curl_cffi Session, falling back to requests.Session: %s", err)
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def close(self) -> None:
        """Close the persistent HTTP session."""
        with self._lock:
            with contextlib.suppress(Exception):
                if self._http_session is not None and hasattr(self._http_session, "close"):
                    self._http_session.close()
            self._http_session = None

    def _reset_http_session(self) -> None:
        """Close and recreate the persistent HTTP session after a Cloudflare block or connection error."""
        with self._lock:
            with contextlib.suppress(Exception):
                if self._http_session is not None and hasattr(self._http_session, "close"):
                    self._http_session.close()
            self._http_session = self._create_http_session()

    def _http_get(self, url: str, **kwargs: Any) -> Any:
        """Perform an HTTP GET using the persistent session (or requests.get if mocked in tests)."""
        if hasattr(requests.get, "_mock_name") or hasattr(requests.get, "side_effect"):
            return requests.get(url, **kwargs)
        with self._lock:
            if self._http_session is None:
                self._http_session = self._create_http_session()
            return self._http_session.get(url, **kwargs)

    def _http_post(self, url: str, **kwargs: Any) -> Any:
        """Perform an HTTP POST using the persistent session (or requests.post if mocked in tests)."""
        if hasattr(requests.post, "_mock_name") or hasattr(requests.post, "side_effect"):
            return requests.post(url, **kwargs)
        with self._lock:
            if self._http_session is None:
                self._http_session = self._create_http_session()
            return self._http_session.post(url, **kwargs)

    def is_recently_sent_text(self, text: str | None, window_seconds: float = 45.0) -> bool:
        """Return True if exact text was recently sent by this client to avoid self-reply loops."""
        clean = (text or "").strip()
        if not clean:
            return False
        now = time.time()
        with self._lock:
            for ts, sent_text in self._sent_message_texts:
                if (now - ts) <= window_seconds and sent_text == clean:
                    return True
        return False

    def _is_rate_limited_response(self, resp: Any) -> bool:
        """Return True if the HTTP response indicates a 429 or Cloudflare rate-limit block."""
        status = getattr(resp, "status_code", 0)
        if status == 429:
            return True
        if status in (403, 503):
            text = getattr(resp, "text", "") or ""
            if "Just a moment..." in text or "cf-mitigated" in text or "Attention Required! | Cloudflare" in text:
                return True
        return False

    def _note_gcs_rate_limit(self, endpoint: str, status_code: int, cooldown_seconds: float = 30.0) -> None:
        """Record a GCS rate-limit cooldown window and log a throttled warning."""
        now = time.time()
        self._gcs_rate_limited_until = max(self._gcs_rate_limited_until, now + cooldown_seconds)
        if (now - self._last_rate_limit_log_ts) > 60.0:
            self._last_rate_limit_log_ts = now
            _LOGGER.warning(
                "Garmin GCS rate limit (HTTP %s on %s); backing off background polls for %.0fs",
                status_code,
                endpoint,
                cooldown_seconds,
            )
        else:
            _LOGGER.debug(
                "Garmin GCS rate limit (HTTP %s on %s); cooldown active",
                status_code,
                endpoint,
            )

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
            self._raw_token_data = self.get_token_data()
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

    def _ensure_diauth_token(self) -> str | None:
        """Ensure a valid diauth Garmin Jr token exists."""
        with self._lock:
            now = time.time()
            if self._diauth_token and self._diauth_expires_at and (now + 300) < self._diauth_expires_at:
                return self._diauth_token
            if now < self._auth_backoff_until:
                return None

            di_token = self._extract_di_token()
            if not di_token:
                return None

            try:
                url = "https://diauth.garmin.com/di-oauth2-service/oauth/token"
                data = {
                    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "subject_token": di_token,
                    "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                    "client_id": "VIVOFIT_JR_ANDROID",
                }
                resp = self._http_post(url, data=data, timeout=15)
                if resp.status_code == 200:
                    res_data = resp.json()
                    self._diauth_token = res_data.get("access_token")
                    expires_in = res_data.get("expires_in", 21600)
                    self._diauth_expires_at = now + expires_in
                    self._auth_backoff_until = 0.0
                    _LOGGER.debug("Exchanged DI token for Garmin Jr diauth token successfully")
                    return self._diauth_token
                if resp.status_code in (400, 401, 403):
                    self._auth_backoff_until = now + 60.0
                _LOGGER.warning("diauth token exchange returned %s: %s", resp.status_code, resp.text)
            except Exception as err:
                _LOGGER.warning("Exception during diauth token exchange: %s", err)

            return None

    def _get_kid_diauth_token(self, kid_connect_id: str | int) -> str | None:
        """Exchange parent DI token for child-specific DiOAuth token to query device settings."""
        with self._lock:
            cid = str(kid_connect_id)
            now = time.time()
            cached = self._kid_diauth_tokens.get(cid)
            if cached and (now + 300) < cached.get("expires_at", 0):
                return cached.get("token")
            if now < self._auth_backoff_until:
                return None

            parent_di = self._ensure_diauth_token()
            if not parent_di:
                return None

            try:
                url = "https://diauth.garmin.com/di-oauth2-service/oauth/token"
                data = {
                    "grant_type": "https://connectapi.garmin.com/di-oauth2-service/oauth/grant/gc_kid",
                    "access_token": parent_di,
                    "gc_kid_id": int(cid),
                    "client_id": "VIVOFIT_JR_ANDROID",
                }
                resp = self._http_post(url, data=data, timeout=15)
                if resp.status_code == 200:
                    res_data = resp.json()
                    kid_token = res_data.get("access_token")
                    expires_in = res_data.get("expires_in", 21600)
                    self._kid_diauth_tokens[cid] = {
                        "token": kid_token,
                        "expires_at": now + expires_in,
                    }
                    _LOGGER.debug("Exchanged kid DiOAuth token for connectId %s successfully", cid)
                    return kid_token
                _LOGGER.debug("Kid DiOAuth exchange returned HTTP %s for connectId %s: %s", resp.status_code, cid, resp.text)
            except Exception as err:
                _LOGGER.debug("Exception during kid DiOAuth exchange for %s: %s", cid, err)

            return None

    def fetch_kid_device_settings(self, kid_connect_id: str | int, device_id: str | int) -> dict[str, Any]:
        """Fetch child's live DeviceSettings / SchoolMode from Garmin connectapi."""
        kid_token = self._get_kid_diauth_token(kid_connect_id)
        if not kid_token:
            return {}

        try:
            url = f"https://connectapi.garmin.com/device-service/deviceservice/device-info/settings/{device_id}"
            headers = {
                "Authorization": f"Bearer {kid_token}",
                "User-Agent": "Vivokid 7.5 245",
                "Accept": "application/json",
            }
            resp = self._http_get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                settings = resp.json()
                _LOGGER.debug("Fetched live kid device settings for device %s: %s", device_id, settings.get("schoolMode"))
                return settings
            else:
                _LOGGER.debug("Fetch kid device settings returned HTTP %s for device %s: %s", resp.status_code, device_id, resp.text)
        except Exception as err:
            _LOGGER.debug("Exception fetching kid device settings for %s: %s", device_id, err)

        return {}

    def _ensure_it_token(self) -> None:
        """Ensure a valid IT token exists, exchanging via connectToIT or refreshing."""
        with self._lock:
            now = time.time()
            if self._it_token and self._it_expires_at and (now + 300) < self._it_expires_at:
                return
            if now < self._auth_backoff_until:
                return

            saw_auth_error = False

            # 1. Try refresh token if available
            if self._it_refresh_token:
                try:
                    url = f"{SERVICES_BASE_URL}/oauthTokenExchangeService/refreshToken"
                    headers = {
                        "Content-Type": "application/json",
                        "User-Agent": "Vivokid 7.5 245",
                        "X-garmin-client-id": "VIVOFIT_JR_ANDROID",
                    }
                    data = {
                        "refreshToken": self._it_refresh_token,
                        "itOAuthClientId": "VIVOFIT_JR_ANDROID",
                    }
                    resp = self._http_post(url, headers=headers, json=data, timeout=15)
                    if resp.status_code == 200:
                        res_data = resp.json()
                        self._it_token = res_data.get("accessToken") or res_data.get("access_token")
                        self._it_refresh_token = res_data.get("refreshToken") or res_data.get("refresh_token", self._it_refresh_token)
                        expires_in = res_data.get("expiresIn") or res_data.get("expires_in", 7776000)
                        self._it_expires_at = now + expires_in
                        self._auth_backoff_until = 0.0
                        self._raw_token_data = self.get_token_data()
                        _LOGGER.debug("Refreshed Garmin IT OAuth2 token successfully")
                        return
                    if resp.status_code in (400, 401, 403):
                        saw_auth_error = True
                    _LOGGER.debug("Garmin IT token refresh failed: %s %s", resp.status_code, resp.text)
                except Exception as err:
                    _LOGGER.debug("Exception during IT token refresh: %s", err)

            # 2. Exchange di_token for IT OAuth2 via connect2_exchange
            for cand_token in [self._extract_di_token(), self._ensure_diauth_token()]:
                if not cand_token:
                    continue
                try:
                    ex_url = f"{SERVICES_BASE_URL}/api/oauth/token?grant_type=connect2_exchange"
                    ex_data = {
                        "client_id": "VIVOFIT_JR_ANDROID",
                        "connect_access_token": cand_token,
                    }
                    ex_headers = {
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept-Encoding": "en_US",
                        "User-Agent": "Vivokid 7.5 245",
                    }
                    ex_res = self._http_post(ex_url, data=ex_data, headers=ex_headers, timeout=15)
                    if ex_res.status_code == 200:
                        ex_json = ex_res.json()
                        self._it_token = ex_json.get("access_token")
                        self._it_refresh_token = ex_json.get("refresh_token")
                        expires_in = ex_json.get("expires_in", 7776000)
                        self._it_expires_at = now + expires_in
                        self._auth_backoff_until = 0.0
                        self._raw_token_data = self.get_token_data()
                        _LOGGER.info("Obtained Garmin IT OAuth2 token via connect2_exchange successfully")
                        return
                    if ex_res.status_code in (400, 401, 403):
                        saw_auth_error = True
                    _LOGGER.debug("connect2_exchange returned %s: %s", ex_res.status_code, ex_res.text)
                except Exception as err:
                    _LOGGER.debug("Exception during connect2_exchange token exchange: %s", err)

            if saw_auth_error:
                self._auth_backoff_until = now + 60.0

    def _get_diauth_headers(self) -> dict[str, str]:
        """Get headers for Garmin Jr / Vivokid API endpoints."""
        token = self._ensure_diauth_token() or self._extract_di_token() or ""
        return {
            "Authorization": f"Bearer {token}",
            "User-Agent": "Vivokid 7.5 245",
            "Accept": "application/json",
        }

    def _get_it_headers(self) -> dict[str, str]:
        """Get headers for Garmin LTE / GCS API endpoints."""
        self._ensure_it_token()
        token = self._it_token or self._ensure_diauth_token() or self._extract_di_token() or ""
        return {
            "Authorization": f"Bearer {token}",
            "User-Agent": "Vivokid 7.5 245",
            "Accept": "application/json",
            "X-garmin-client-id": "VIVOFIT_JR_ANDROID",
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

            headers = self._get_diauth_headers()
            resp = self._http_get(
                f"{VIVOKID_BASE_URL}/v3/family/info", headers=headers, timeout=10
            )
            if resp.status_code == 200:
                return True

            devices = self.client.connectapi("/device-service/deviceregistration/devices")
            return isinstance(devices, list)
        except Exception as err:
            _LOGGER.debug("Session validation failed: %s", err)
            return False

    def fetch_trackpoints(self, kid_profile_id: str | int, limit: int = 50, connect_id: str | int | None = None) -> list[dict[str, Any]]:
        """Fetch latest GPS trackpoints for a child profile from GCS Tracker API.

        The tracker API requires the kid's connectId (not the Garmin Jr kid id).
        If connect_id is provided, it is used; otherwise kid_profile_id is used.
        """
        tracker_id = str(connect_id) if connect_id else str(kid_profile_id)
        if not tracker_id or tracker_id in ("None", "null", ""):
            _LOGGER.warning("Cannot fetch trackpoints for kid %s: missing connectId", kid_profile_id)
            return []
        if time.time() < self._gcs_rate_limited_until:
            _LOGGER.debug("Skipping fetch_trackpoints for kid %s: GCS rate-limit cooldown active", kid_profile_id)
            return []
        try:
            headers = self._get_it_headers()
            url = f"{GCS_API_BASE_URL}/tracker/family/api/v1/trackpoints"
            begin = (
                datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=36)
            ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            params: dict[str, Any] = {"kidProfileId": tracker_id, "begin": begin, "limit": max(1, int(limit))}
            resp = self._http_get(url, headers=headers, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
                if isinstance(data, dict):
                    return data.get("trackPoints") or data.get("points") or []
            elif self._is_rate_limited_response(resp):
                self._note_gcs_rate_limit("trackpoints", resp.status_code)
            else:
                _LOGGER.warning("Trackpoints API returned HTTP %s for kid %s (connectId=%s): %s", resp.status_code, kid_profile_id, tracker_id, resp.text[:100])
        except Exception as err:
            _LOGGER.warning("Could not fetch trackpoints for kid %s (connectId=%s): %s", kid_profile_id, tracker_id, err)
        return []

    def fetch_geofences(self, kid_profile_id: str | int | None = None) -> list[dict[str, Any]]:
        """Fetch all configured Garmin Safe Zones / Geofences from Vivokid API."""
        try:
            headers = self._get_diauth_headers()
            url = f"{VIVOKID_BASE_URL}/geofence/all"
            resp = self._http_get(url, headers=headers, timeout=10)
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

    def fetch_messages(self, after_iso: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Fetch message history from GCS Messaging API."""
        if time.time() < self._gcs_rate_limited_until:
            _LOGGER.debug("Skipping fetch_messages: GCS rate-limit cooldown active")
            self.last_messages_fetch_ok = False
            return []
        try:
            headers = self._get_it_headers()
            url = f"{GCS_API_BASE_URL}/messaging/family/api/v1/guardian/messages"
            if not after_iso:
                # Default to 12 hours ago so ascending pagination always includes today's latest messages
                after_iso = (
                    datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=12)
                ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            params: dict[str, Any] = {"after": after_iso, "limit": max(1, int(limit))}
            resp = self._http_get(url, headers=headers, params=params, timeout=10)
            if resp.status_code == 200:
                self.last_messages_fetch_ok = True
                data = resp.json()
                result_msgs: list[dict[str, Any]] = []
                if isinstance(data, list):
                    result_msgs = data
                elif isinstance(data, dict):
                    msgs = (
                        data.get("messages")
                        or data.get("messageList")
                        or data.get("items")
                        or data.get("conversationMessages")
                        or []
                    )
                    if msgs:
                        result_msgs = msgs
                    else:
                        convs = data.get("conversations") or []
                        all_c_msgs: list[dict[str, Any]] = []
                        for c in convs:
                            all_c_msgs.extend(c.get("messages") or c.get("recentMessages") or [])
                        if all_c_msgs:
                            result_msgs = all_c_msgs

                _LOGGER.debug("GCS fetch_messages returned %d message(s)", len(result_msgs))
                if result_msgs:
                    result_msgs.sort(
                        key=lambda m: str(m.get("createDateTime") or m.get("createdTimestamp") or m.get("timestamp") or ""),
                        reverse=True,
                    )
                    return result_msgs
                return []
            self.last_messages_fetch_ok = False
            if self._is_rate_limited_response(resp):
                self._note_gcs_rate_limit("guardian/messages", resp.status_code)
                return []
            if resp.status_code in (401, 403, 500, 502, 503, 504):
                _LOGGER.debug("GCS guardian/messages returned HTTP %s (skipping fallback): %s", resp.status_code, resp.text[:100])
                return []

            _LOGGER.debug("GCS guardian/messages returned HTTP %s: %s", resp.status_code, resp.text[:100])

            # Fallback: Query active conversations list only if guardian endpoint returned 404/unsupported
            try:
                c_url = f"{GCS_API_BASE_URL}/messaging/family/api/v1/conversations"
                c_resp = self._http_get(c_url, headers=headers, timeout=10)
                if c_resp.status_code == 200:
                    self.last_messages_fetch_ok = True
                    c_data = c_resp.json()
                    conv_list = c_data if isinstance(c_data, list) else (c_data.get("conversations") or [])
                    fallback_msgs: list[dict[str, Any]] = []
                    for conv in conv_list:
                        c_msgs = conv.get("messages") or conv.get("recentMessages") or []
                        if not c_msgs and conv.get("conversationId"):
                            cid = conv.get("conversationId")
                            m_resp = self._http_get(
                                f"{GCS_API_BASE_URL}/messaging/family/api/v1/conversation/{cid}/messages",
                                headers=headers,
                                params=params,
                                timeout=10,
                            )
                            if m_resp.status_code == 200:
                                m_json = m_resp.json()
                                c_msgs = m_json if isinstance(m_json, list) else (m_json.get("messages") or [])
                        fallback_msgs.extend(c_msgs)
                    if fallback_msgs:
                        return fallback_msgs
            except Exception as c_err:
                _LOGGER.debug("Conversations fallback error: %s", c_err)

        except Exception as err:
            self.last_messages_fetch_ok = False
            _LOGGER.debug("Could not fetch messages from GCS API: %s", err)
        return []

    def parse_child_messages(
        self,
        recent_messages: list[dict[str, Any]],
        kid_id: str | int,
        connect_id: str | int | None = None,
        device_id: str | int | None = None,
        user_profile_pk: str | int | None = None,
        total_family_kids: int = 0,
    ) -> list[dict[str, Any]]:
        """Filter and sort messages for a specific child newest-first."""
        if total_family_kids == 0:
            _LOGGER.debug("Garmin Jr: total_family_kids is 0 during message parsing for kid %s", kid_id)

        with self._lock:
            guardian_pks = set(self._guardian_profile_pks)
            learned_pk = self._learned_child_pks.get(str(kid_id))

        kid_identifiers = ({
            str(kid_id),
            str(connect_id or ""),
            str(device_id or ""),
            str(user_profile_pk or ""),
            str(learned_pk or ""),
        } - {"", "None", "null"}) - guardian_pks

        child_messages: list[dict[str, Any]] = []
        for msg in recent_messages:
            to_pk = str(msg.get("toUserProfilePk", ""))
            from_pk = str(msg.get("fromUserProfilePk", ""))
            matched = False

            if to_pk in kid_identifiers or from_pk in kid_identifiers:
                matched = True
            elif total_family_kids == 1:
                # Single-child household: match if message involves a known guardian and the watch
                if from_pk in guardian_pks and to_pk and to_pk not in guardian_pks:
                    matched = True
                elif to_pk in guardian_pks and from_pk and from_pk not in guardian_pks:
                    matched = True
                elif not guardian_pks and (from_pk or to_pk):
                    # Fallback when guardian PKs could not be loaded from API: only match if unambiguous watch audio note
                    if msg.get("mediaType") == "audio/amr" or (msg.get("mediaType") in ("Audio", "audio/amr") and msg.get("transcription")):
                        matched = True

            if matched:
                child_messages.append(msg)
                # Learn the observed child profile PK positively attributed to the watch,
                # ONLY when guardian PKs are known so a guardian can never be misbound as the child.
                watch_pk = None
                if from_pk in kid_identifiers and from_pk not in guardian_pks and from_pk != str(kid_id):
                    watch_pk = from_pk
                elif to_pk in kid_identifiers and to_pk not in guardian_pks and to_pk != str(kid_id):
                    watch_pk = to_pk
                elif from_pk in guardian_pks and to_pk and to_pk not in guardian_pks and to_pk != str(kid_id):
                    # Parent sent to watch -> to_pk is the watch
                    watch_pk = to_pk
                elif to_pk in guardian_pks and from_pk and from_pk not in guardian_pks and from_pk != str(kid_id):
                    # Watch sent to parent -> from_pk is the watch
                    watch_pk = from_pk

                if watch_pk and guardian_pks and watch_pk not in guardian_pks:
                    with self._lock:
                        already_bound_child = next(
                            (c_id for c_id, pk in self._learned_child_pks.items() if pk == watch_pk and c_id != str(kid_id)),
                            None,
                        )
                        if already_bound_child:
                            _LOGGER.debug(
                                "Skipping PK binding %s for child %s: already bound to child %s",
                                watch_pk,
                                kid_id,
                                already_bound_child,
                            )
                        elif str(kid_id) not in self._learned_child_pks or self._learned_child_pks[str(kid_id)] != watch_pk:
                            self._learned_child_pks[str(kid_id)] = watch_pk
                            _LOGGER.debug("Learned and bound profile PK %s for child %s", watch_pk, kid_id)

        child_messages.sort(
            key=lambda m: str(m.get("createDateTime") or m.get("createdTimestamp") or m.get("timestamp") or ""),
            reverse=True,
        )
        return child_messages

    def _post_with_retry(
        self,
        url: str,
        endpoint_name: str,
        target_label: str | int,
        success_codes: tuple[int, ...],
        json_payload: dict[str, Any] | None = None,
    ) -> bool:
        """Execute a GCS POST request with bounded retry/backoff on transient 429/5xx/network errors."""
        max_attempts = len(self._send_retry_delays) + 1
        for attempt in range(max_attempts):
            try:
                headers = self._get_it_headers()
                post_kwargs: dict[str, Any] = {"headers": headers, "timeout": 15}
                if json_payload is not None:
                    headers["Content-Type"] = "application/json"
                    post_kwargs["json"] = json_payload

                resp = self._http_post(url, **post_kwargs)
                if resp.status_code in success_codes:
                    return True

                is_rate_limit = self._is_rate_limited_response(resp)
                if is_rate_limit:
                    self._note_gcs_rate_limit(endpoint_name, resp.status_code)

                if (is_rate_limit or resp.status_code in (500, 502, 503, 504)) and attempt < len(self._send_retry_delays):
                    delay = self._send_retry_delays[attempt]
                    _LOGGER.warning(
                        "Transient HTTP %s on %s for %s (attempt %d/%d); retrying in %.1fs",
                        resp.status_code,
                        endpoint_name,
                        target_label,
                        attempt + 1,
                        max_attempts,
                        delay,
                    )
                    time.sleep(delay)
                    continue

                _LOGGER.warning("Failed %s for %s: %s %s", endpoint_name, target_label, resp.status_code, resp.text[:200])
                return False
            except Exception as err:
                self._reset_http_session()
                if attempt < len(self._send_retry_delays):
                    delay = self._send_retry_delays[attempt]
                    _LOGGER.warning(
                        "Error on %s for %s (attempt %d/%d: %s); retrying in %.1fs",
                        endpoint_name,
                        target_label,
                        attempt + 1,
                        max_attempts,
                        err,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                _LOGGER.error("Error on %s for %s: %s", endpoint_name, target_label, err)
        return False

    def send_text_message(self, to_user_profile_pk: str | int, message_text: str) -> bool:
        """Send a text message to a child's Garmin Bounce watch with retry on 429/5xx."""
        if not str(to_user_profile_pk).isdigit():
            _LOGGER.warning("Cannot send text message: invalid non-numeric userProfilePk '%s'", to_user_profile_pk)
            return False

        url = f"{GCS_API_BASE_URL}/messaging/family/api/v1/messages/user/text"
        payload = {
            "toUserProfilePk": int(to_user_profile_pk),
            "messageText": message_text,
        }
        sent = self._post_with_retry(
            url,
            endpoint_name="messages/user/text",
            target_label=to_user_profile_pk,
            success_codes=(200, 201, 204),
            json_payload=payload,
        )
        if sent:
            clean = (message_text or "").strip()
            if clean:
                with self._lock:
                    self._sent_message_texts.append((time.time(), clean))
            _LOGGER.info("Successfully sent text message to profile %s", to_user_profile_pk)
        return sent

    def request_location_update(self, device_or_kid_id: str | int) -> bool:
        """Ping the child's watch over LTE to request an immediate GPS location refresh."""
        url = f"{GCS_API_BASE_URL}/device-instruction/api/v1/family/{device_or_kid_id}/update-location"
        sent = self._post_with_retry(
            url,
            endpoint_name="update-location",
            target_label=device_or_kid_id,
            success_codes=(200, 201, 202, 204),
            json_payload=None,
        )
        if sent:
            _LOGGER.debug("Location refresh instruction dispatched to %s", device_or_kid_id)
        return sent

    def fetch_all_data(self, local_today: str | None = None) -> dict[str, dict[str, Any]]:
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
                with self._lock:
                    self._raw_token_data = self.get_token_data()
            except GarminConnectAuthenticationError as auth_err:
                raise GarminJrAuthError(f"Garmin session refresh failed: {auth_err}") from auth_err
            except Exception as err:
                _LOGGER.warning("Token refresh warning: %s", err)

        results: dict[str, dict[str, Any]] = {}
        diauth_headers = self._get_diauth_headers()
        today = local_today or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        utc_now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # 1. Fetch Garmin Jr Family Info
        family_id = self._family_id
        family_name = self._family_name
        kids_list: list[dict[str, Any]] = []
        family_geofences: list[dict[str, Any]] = []

        try:
            fam_resp = self._http_get(f"{VIVOKID_BASE_URL}/v3/family/info", headers=diauth_headers, timeout=10)
            if fam_resp.status_code == 401:
                _LOGGER.warning("Garmin Jr family/info returned HTTP 401; attempting session refresh")
                try:
                    self.client._refresh_session()
                except Exception as ref_err:
                    raise GarminJrAuthError(f"Garmin authentication expired (HTTP 401): {ref_err}") from ref_err
                with self._lock:
                    self._diauth_token = None
                    self._diauth_expires_at = None
                    self._auth_backoff_until = 0.0
                    self._raw_token_data = self.get_token_data()
                diauth_headers = self._get_diauth_headers()
                fam_resp = self._http_get(f"{VIVOKID_BASE_URL}/v3/family/info", headers=diauth_headers, timeout=10)
                if fam_resp.status_code == 401:
                    raise GarminJrAuthError("Garmin authentication expired or revoked (HTTP 401)")

            if fam_resp.status_code == 200:
                fam_data = fam_resp.json()
                families = fam_data.get("families", [])
                if families:
                    family = families[0]
                    family_id = family.get("familyId")
                    family_name = family.get("name")
                    kids_list = family.get("kids", [])
                    family_geofences = family.get("geofences") or []
                    guardians = family.get("guardians") or fam_data.get("guardians") or []
                elif fam_data.get("family"):
                    family = fam_data.get("family", {})
                    family_id = family.get("familyId")
                    family_name = family.get("name")
                    kids_list = family.get("kids", [])
                    family_geofences = family.get("geofences") or []
                    guardians = family.get("guardians") or fam_data.get("guardians") or []
                else:
                    family_geofences = []
                    guardians = []

                with self._lock:
                    if family_id:
                        self._family_id = family_id
                    if family_name:
                        self._family_name = family_name
                    if kids_list:
                        self.family_kids_count = len(kids_list)
                        for k in kids_list:
                            if k.get("id") is not None:
                                kid_key = str(k.get("id"))
                                merged_kid = dict(self._known_kids.get(kid_key, {}))
                                merged_kid.update(k)
                                self._known_kids[kid_key] = merged_kid

                    for g in guardians:
                        for key in ("userProfilePk", "userProfilePK", "profilePk", "userProfileId"):
                            v = g.get(key) or (g.get("account") or {}).get(key)
                            if v:
                                self._guardian_profile_pks.add(str(v))

                    if fam_data.get("userProfilePk"):
                        self._guardian_profile_pks.add(str(fam_data.get("userProfilePk")))

                    # Purge any previously misbound guardian PK from _learned_child_pks
                    for cid in [c for c, pk in self._learned_child_pks.items() if str(pk) in self._guardian_profile_pks]:
                        del self._learned_child_pks[cid]

                _LOGGER.debug("Discovered Garmin Jr Family: %s (ID: %s, Kids: %d, Guardians: %s)", family_name, family_id, self.family_kids_count, len(self._guardian_profile_pks))
        except GarminJrAuthError:
            raise
        except Exception as err:
            _LOGGER.warning("Error querying Garmin Jr family info: %s", err)
            family_geofences = []

        with self._lock:
            if not kids_list and self._known_kids:
                kids_list = list(self._known_kids.values())
                if not self.family_kids_count:
                    self.family_kids_count = len(kids_list)
                _LOGGER.debug("Restored %d child profile(s) from cached family info", len(kids_list))

        # 2. Fetch Recent Messages across the family
        recent_messages: list[dict[str, Any]] = []
        try:
            recent_messages = self.fetch_messages(limit=100)
        except Exception as msg_err:
            _LOGGER.debug("Error fetching recent messages: %s", msg_err)

        # 3. Fetch Garmin Safe Zones / Geofences across the family
        all_geofences: list[dict[str, Any]] = []
        geofence_by_id: dict[str, dict[str, Any]] = {}
        try:
            all_geofences = self.fetch_geofences()
            # Fallback: use geofences from family info if /geofence/all returned empty
            if not all_geofences and family_geofences:
                for g in family_geofences:
                    all_geofences.append({
                        "id": g.get("geofenceId") or g.get("id"),
                        "name": g.get("name"),
                        "latitude": g.get("latitude") or g.get("lat"),
                        "longitude": g.get("longitude") or g.get("lon"),
                        "radius": g.get("radius"),
                        "wifi_ssid": g.get("wifiSsid"),
                        "kid_ids": g.get("kidIds", []),
                    })
                _LOGGER.debug("Loaded %d geofences from family info fallback", len(all_geofences))
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
                    # Extract connectId strictly from connectId fields (used by Tracker API)
                    connect_id = k.get("connectId") or (k.get("account") or {}).get("connectId")

                    # Extract userProfilePk from profile keys, learned bindings, or connectId fallback for messaging
                    user_profile_pk = (
                        k.get("userProfilePk")
                        or k.get("userProfilePK")
                        or k.get("profilePk")
                        or k.get("userProfileId")
                        or self._learned_child_pks.get(k_id)
                        or connect_id
                    )
                    device_id = k.get("deviceId") or k.get("device_id")
                    device_settings: dict[str, Any] = {}
                    if connect_id and device_id:
                        device_settings = self.fetch_kid_device_settings(connect_id, device_id)
                    elif device_id:
                        _LOGGER.debug("Skipping fetch_kid_device_settings for kid %s: missing connectId", k_id)

                    kids_map[k_id] = {
                        "id": k_id,
                        "child_id": k_id,
                        "connectId": connect_id,
                        "connect_id": connect_id,
                        "userProfilePk": user_profile_pk,
                        "user_profile_pk": user_profile_pk,
                        "displayName": k.get("name") or "Child",
                        "child_name": k.get("name") or "Child",
                        "deviceId": device_id,
                        "device_id": device_id,
                        "hasLteDevice": k.get("hasLteDevice", True),
                        "totalPoints": k.get("totalPoints"),
                        "settings": device_settings or k.get("settings") or {},
                        "school_mode": device_settings.get("schoolMode") or k.get("schoolMode") or (k.get("settings") or {}).get("schoolMode") or {},
                        "bed_time": k.get("bedTime") or (k.get("settings") or {}).get("bedTime"),
                        "wake_time": k.get("wakeTime") or (k.get("settings") or {}).get("wakeTime"),
                        "dnd_settings": k.get("dndSettings") or (k.get("settings") or {}).get("dndSettings") or {},
                        "account": k.get("account") or {},
                    }

                lb_url = f"{VIVOKID_BASE_URL}/v2/leaderboard/daily/{family_id}/{today}"
                lb_resp = self._http_get(lb_url, headers=diauth_headers, timeout=10)
                if lb_resp.status_code == 200:
                    for kid in lb_resp.json().get("kidStepsData", []):
                        k_id = str(kid.get("id"))
                        if k_id in kids_map:
                            kids_map[k_id].update(kid)
                        else:
                            base_kid = dict(self._known_kids.get(k_id, {}))
                            base_kid.update(kid)
                            kids_map[k_id] = base_kid

                for kid_id, kid in kids_map.items():
                    with self._lock:
                        prev_record = dict(self._last_results.get(kid_id, {}))
                    kid_name = kid.get("displayName") or kid.get("name") or prev_record.get("child_name") or "Child"
                    live_steps = kid.get("steps") if kid.get("steps") is not None else (prev_record.get("steps") or 0)
                    last_sync = kid.get("lastSyncDate") or prev_record.get("last_sync")

                    # Filter geofences applicable to this kid (handle int/str ID differences safely)
                    kid_id_str = str(kid_id)
                    kid_geofences = [
                        g for g in all_geofences
                        if not g.get("kid_ids") or any(str(gid) == kid_id_str for gid in g.get("kid_ids", []))
                    ] or prev_record.get("geofences", [])

                    # Fetch Daily Step Summary
                    step_goal = prev_record.get("daily_step_goal") or 7500
                    steps_record = prev_record.get("steps_record")
                    active_mins = prev_record.get("active_minutes") or 0
                    try:
                        sum_url = f"{VIVOKID_BASE_URL}/v2/activity/summary/kid/{kid_id}/{today}"
                        sum_resp = self._http_get(sum_url, headers=diauth_headers, timeout=10)
                        if sum_resp.status_code == 200:
                            sum_data = sum_resp.json()
                            step_goal = sum_data.get("stepsGoal") or step_goal
                            steps_record = sum_data.get("stepsRecord") or steps_record
                            active_mins = (
                                sum_data.get("activeMinutes")
                                or sum_data.get("activeMinute")
                                or ((sum_data.get("walkingMinutes") or 0) + (sum_data.get("runningMinutes") or 0))
                                or 0
                            )
                            if sum_data.get("lastSyncDate"):
                                ts_ms = sum_data.get("lastSyncDate")
                                last_sync = datetime.datetime.fromtimestamp(
                                    ts_ms / 1000.0, tz=datetime.timezone.utc
                                ).isoformat()
                    except Exception as sum_err:
                        _LOGGER.debug("Could not fetch kid summary for %s: %s", kid_id, sum_err)

                    # Fetch Personal Records
                    active_mins_record = prev_record.get("active_minutes_record")
                    try:
                        pr_url = f"{VIVOKID_BASE_URL}/v2/activity/personalrecords/{kid_id}"
                        pr_resp = self._http_get(pr_url, headers=diauth_headers, timeout=10)
                        if pr_resp.status_code == 200:
                            pr_data = pr_resp.json()
                            steps_record = pr_data.get("stepsRecord") or steps_record
                            active_mins_record = pr_data.get("activeMinuteRecord") or active_mins_record
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

                    # Fetch Live GPS Trackpoints & Resolve Geofence Status
                    kid_connect_id = (
                        kid.get("connectId")
                        or kid.get("connect_id")
                        or (kid.get("account") or {}).get("connectId")
                        or prev_record.get("connectId")
                        or prev_record.get("connect_id")
                    )
                    trackpoints = self.fetch_trackpoints(kid_id, limit=200, connect_id=kid_connect_id)
                    loc_resolved = resolve_trackpoints_and_safe_zone(trackpoints, kid_geofences, prev_record=prev_record)
                    latitude = loc_resolved["latitude"]
                    longitude = loc_resolved["longitude"]
                    gps_accuracy = loc_resolved["gps_accuracy"]
                    location_ts = loc_resolved["location_timestamp"]
                    fix_type = loc_resolved["fix_type"]
                    active_geofence_id = loc_resolved["garmin_geofence_id"]
                    active_geofence_name = loc_resolved["garmin_safe_zone"]
                    has_wifi = loc_resolved["has_wifi"]
                    geofence_lat = loc_resolved["geofence_latitude"]
                    geofence_lon = loc_resolved["geofence_longitude"]
                    geofence_radius = loc_resolved["geofence_radius"]

                    if loc_resolved["tp_battery"] is not None and battery_level is None:
                        battery_level = loc_resolved["tp_battery"]
                    if loc_resolved["tp_charging"]:
                        battery_status = "CHARGING"
                    if not trackpoints and battery_level is None and prev_record:
                        battery_level = prev_record.get("battery_level")
                        battery_status = prev_record.get("battery_status", "NORMAL")

                    # Match latest message for this child
                    last_msg_text = None
                    last_msg_time = None
                    last_msg_sender = None
                    last_msg_media = None

                    child_messages = self.parse_child_messages(
                        recent_messages,
                        kid_id=kid_id,
                        connect_id=kid.get("connectId") or kid_connect_id,
                        device_id=kid.get("deviceId") or kid.get("device_id") or prev_record.get("device_id"),
                        user_profile_pk=kid.get("userProfilePk") or self._learned_child_pks.get(kid_id) or prev_record.get("userProfilePk"),
                        total_family_kids=self.family_kids_count or len(kids_map),
                    )

                    if child_messages:
                        latest_msg = child_messages[0]
                        last_msg_text = extract_message_text(latest_msg, include_audio_placeholder=True)
                        last_msg_time = latest_msg.get("createDateTime") or latest_msg.get("createdTimestamp") or latest_msg.get("timestamp")
                        from_pk = str(latest_msg.get("fromUserProfilePk", ""))
                        with self._lock:
                            guardian_pks = set(self._guardian_profile_pks)
                            learned_pk = self._learned_child_pks.get(kid_id)
                        kid_identifiers = ({
                            str(kid_id),
                            str(kid_connect_id or ""),
                            str(kid.get("deviceId") or ""),
                            str(kid.get("device_id") or ""),
                            str(kid.get("userProfilePk") or ""),
                            str(learned_pk or ""),
                        } - {"", "None", "null"}) - guardian_pks
                        last_msg_sender = (
                            latest_msg.get("senderDisplayName")
                            or latest_msg.get("sender")
                            or ("Child" if from_pk in kid_identifiers else "Guardian")
                        )
                        last_msg_media = latest_msg.get("mediaType", "Text")
                    elif prev_record:
                        last_msg_text = prev_record.get("last_message")
                        last_msg_time = prev_record.get("last_message_time")
                        last_msg_sender = prev_record.get("last_message_sender")
                        last_msg_media = prev_record.get("last_message_media")

                    resolved_pk = (
                        kid.get("user_profile_pk")
                        or kid.get("userProfilePk")
                        or self._learned_child_pks.get(kid_id)
                        or kid.get("connect_id")
                        or kid.get("connectId")
                        or kid_connect_id
                        or prev_record.get("user_profile_pk")
                        or kid_id
                    )
                    connect_id_val = kid.get("connect_id") or kid.get("connectId") or kid_connect_id
                    device_id_val = kid.get("device_id") or kid.get("deviceId") or prev_record.get("device_id") or kid_id
                    kid_settings = kid.get("settings") or prev_record.get("settings") or {}
                    kid_school_mode = kid.get("school_mode") or kid_settings.get("schoolMode") or prev_record.get("school_mode") or {}

                    results[kid_id] = {
                        "child_id": kid_id,
                        "child_name": kid_name,
                        "displayName": kid_name,
                        "connect_id": connect_id_val,
                        "connectId": connect_id_val,
                        "user_profile_pk": resolved_pk,
                        "userProfilePk": resolved_pk,
                        "device_id": device_id_val,
                        "deviceId": device_id_val,
                        "device_name": f"{kid_name}'s Bounce",
                        "model": "Garmin Bounce",
                        "latitude": latitude,
                        "longitude": longitude,
                        "gps_accuracy": gps_accuracy,
                        "location_timestamp": location_ts,
                        "battery_level": battery_level,
                        "battery_status": battery_status,
                        "steps": live_steps,
                        "daily_step_goal": step_goal,
                        "steps_record": steps_record,
                        "active_minutes": active_mins,
                        "active_minutes_record": active_mins_record,
                        "last_sync": last_sync or utc_now_iso,
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
                        "new_messages": child_messages,
                        "settings": kid_settings,
                        "school_mode": kid_school_mode,
                        "bed_time": kid.get("bed_time") or kid_settings.get("bedTime") or prev_record.get("bed_time"),
                        "wake_time": kid.get("wake_time") or kid_settings.get("wakeTime") or prev_record.get("wake_time"),
                        "dnd_settings": kid.get("dnd_settings") or kid_settings.get("dndSettings") or prev_record.get("dnd_settings") or {},
                    }
            except Exception as lb_err:
                _LOGGER.warning("Error fetching kid leaderboard for family %s: %s", family_id, lb_err)

        with self._lock:
            if not results and self._last_results:
                _LOGGER.debug("Preserving %d cached child record(s) after transient family/leaderboard poll failure", len(self._last_results))
                results = {
                    cid: {**cdata, "new_messages": []}
                    for cid, cdata in self._last_results.items()
                }

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

                    # Match to existing child by deviceId or childId
                    matched_child = None
                    if child_id in results:
                        matched_child = child_id
                    else:
                        for cid, cdata in results.items():
                            if cdata.get("device_id") == dev_id or cdata.get("serial_number") == dev_id:
                                matched_child = cid
                                break

                    if matched_child:
                        if dev_battery_level is not None:
                            results[matched_child]["battery_level"] = dev_battery_level
                        if dev_battery_status:
                            results[matched_child]["battery_status"] = dev_battery_status
                        continue

                    # If family kids were already discovered, do not add unrelated adult watches as child entities
                    if results:
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
                        "location_timestamp": None,
                        "battery_level": dev_battery_level,
                        "battery_status": dev_battery_status,
                        "steps": 0,
                        "daily_step_goal": 10000,
                        "steps_record": None,
                        "active_minutes": None,
                        "active_minutes_record": None,
                        "last_sync": dev.get("lastSyncTime") or utc_now_iso,
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

        if results:
            with self._lock:
                self._last_results = {cid: dict(cdata) for cid, cdata in results.items()}

        return results

