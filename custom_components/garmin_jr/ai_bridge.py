"""AI Bridge for Garmin Bounce watches powered by local Strix Halo NPU."""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import logging
import re
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter

from .const import (
    ATTR_CHILD_NAME,
    DEFAULT_GARAGE_COVER_ENTITY,
    DEFAULT_HOME_SAFE_ZONE,
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_URL,
    DOMAIN,
    LOGGER,
)
from .garmin_client import resolve_trackpoints_and_safe_zone
from .plane_spotter import (
    enrich_flight_details,
    fetch_live_aircraft_sync,
    filter_and_rank_planes,
    format_bounce_response,
    resolve_kid_location,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_STRIX_HALO_URL = DEFAULT_LLM_URL
DEFAULT_MODEL = DEFAULT_LLM_MODEL
MAX_HISTORY_TURNS = 6
FLIGHT_CONTEXT_TTL_SECONDS = 1200  # 20 minutes


def _sanitize_prompt_field(val: Any, max_len: int = 60) -> str:
    """Strip control characters and action-spoofing tokens from untrusted external API strings."""
    cleaned = re.sub(r"[\r\n\x00-\x1f]+", " ", str(val or "")).strip()
    cleaned = re.sub(r"\bACTION\s*:", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned[:max_len]


class ChildSession:
    """Maintains conversational context and flight telemetry for a child."""

    def __init__(self, child_id: str) -> None:
        """Initialize child session."""
        self.child_id = child_id
        self.history: list[dict[str, str]] = []
        self.last_spotted_flight: dict[str, Any] | None = None
        self.last_spotted_ts: float = 0.0

    def add_turn(self, role: str, content: str) -> None:
        """Add message to sliding window history."""
        self.history.append({"role": role, "content": content})
        if len(self.history) > MAX_HISTORY_TURNS:
            self.history = self.history[-MAX_HISTORY_TURNS:]

    def set_spotted_flight(self, flight_data: dict[str, Any]) -> None:
        """Cache last spotted flight details for multi-turn technical follow-ups."""
        self.last_spotted_flight = flight_data
        self.last_spotted_ts = time.time()

    def get_spotted_flight_context(self) -> str | None:
        """Get formatted flight context if still fresh (< 20 mins)."""
        if not self.last_spotted_flight:
            return None
        if (time.time() - self.last_spotted_ts) > FLIGHT_CONTEXT_TTL_SECONDS:
            self.last_spotted_flight = None
            return None

        f = self.last_spotted_flight
        airline = _sanitize_prompt_field(f.get("airline") or "Inconnu")
        callsign = _sanitize_prompt_field(f.get("callsign_iata") or f.get("callsign") or "")
        model = _sanitize_prompt_field(f.get("model_name") or "Avion de ligne")
        route = _sanitize_prompt_field(f.get("route") or "Vol privé / nolisé (sans horaire public)", max_len=80)
        alt_ft = int(round(f.get("altitude_ft", 0)))
        alt_ft_str = f"{alt_ft:,}".replace(",", " ")
        speed_kmh = int(round(f.get("speed_kmh", 0)))

        return (
            f"Dernier avion repéré : {airline} ({callsign}), modèle {model}, "
            f"trajet {route}, altitude {alt_ft_str} pi, vitesse {speed_kmh} km/h."
        )


class GarminBounceAiBridge:
    """Direct AI bridge to Strix Halo NPU for Garmin Bounce watch messaging."""

    def __init__(
        self,
        hass: Any,
        base_url: str = DEFAULT_STRIX_HALO_URL,
        model: str = DEFAULT_MODEL,
    ) -> None:
        """Initialize the AI bridge."""
        self.hass = hass
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.sessions: dict[str, ChildSession] = {}
        self.http_session = requests.Session()
        adapter = HTTPAdapter(pool_connections=5, pool_maxsize=10, max_retries=1)
        self.http_session.mount("http://", adapter)
        self.http_session.mount("https://", adapter)

    def close(self) -> None:
        """Close the HTTP session on unload."""
        with contextlib.suppress(Exception):
            self.http_session.close()

    def get_session(self, child_id: str) -> ChildSession:
        """Retrieve or create child session."""
        if child_id not in self.sessions:
            self.sessions[child_id] = ChildSession(child_id)
        return self.sessions[child_id]

    def _build_system_prompt(
        self, child_name: str, child_data: dict[str, Any], session: ChildSession
    ) -> str:
        """Construct a tailored system prompt for a 10-year-old curious about aviation and science."""
        safe_zone = child_data.get("active_geofence_name") or child_data.get("garmin_safe_zone") or "Maison"
        flight_ctx = session.get_spotted_flight_context()

        prompt_lines = [
            f"Tu es l'assistant personnel de {child_name} (10 ans) sur sa montre Garmin Bounce.",
            "Tu ES directement connecté à la domotique Home Assistant de sa maison et aux radars aériens.",
            f"{child_name} aime les explications précises, les chiffres réels et les détails techniques (vitesse en km/h, altitude, passagers), court et clair.",
            "",
            "RÈGLES D'ACTION STRICTES :",
            f"- 1. Si {child_name} demande de repérer, identifier ou voir un avion dans le ciel (ex: \"Quel est cet avion ?\", \"Quel avion passe ?\") : Réponds UNIQUEMENT \"ACTION: SPOT_PLANE\"",
            f"- 2. Si {child_name} demande d'ouvrir la porte du garage ou d'ouvrir (ex: \"Ouvre le garage\", \"Ouvre la porte\", \"Ouvre la\", \"Ouvre pour de vrai\", \"Ouvre-la\", \"Peux-tu ouvrir ?\") : Réponds UNIQUEMENT \"ACTION: OPEN_GARAGE\"",
            f"- 3. Pour toute question sur l'avion déjà repéré (vitesse, destination, altitude, passagers) ou discussion générale : Réponds directement en français en MOINS DE 140 CARACTÈRES sans écrire le mot ACTION.",
            "",
            f"Lieu actuel de {child_name} : {safe_zone}.",
        ]

        if flight_ctx:
            prompt_lines.append(f"Contexte du dernier avion repéré : {flight_ctx}")

        return "\n".join(prompt_lines)

    def process_incoming_message(
        self,
        child_id: str,
        child_name: str,
        incoming_text: str,
        child_data: dict[str, Any],
        base_url: str | None = None,
        model: str | None = None,
        garage_cover_entity: str | None = None,
        home_safe_zone: str | None = None,
        context: Any = None,
    ) -> str:
        """Process incoming voice/text message through Strix Halo NPU and execute appropriate action."""
        session = self.get_session(child_id)
        clean_text = incoming_text.strip()
        if not clean_text:
            return "Reçu! 👍"

        system_prompt = self._build_system_prompt(child_name, child_data, session)

        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        messages.extend(session.history)
        messages.append({"role": "user", "content": clean_text})

        target_url = (base_url or self.base_url).rstrip("/")
        target_model = model or self.model

        payload = {
            "model": target_model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 400,
        }

        # 1. Query Strix Halo NPU with timeout
        raw_reply = None
        try:
            url = f"{target_url}/v1/chat/completions"
            resp = self.http_session.post(url, json=payload, timeout=6.0)
            if resp.status_code == 200:
                data = resp.json()
                raw_reply = (
                    data.get("choices", [{}])[0].get("message", {}).get("content", "")
                ).strip()
            else:
                _LOGGER.warning(
                    "Strix Halo API returned %s: %s", resp.status_code, resp.text
                )
        except Exception as err:
            _LOGGER.warning("Could not reach Strix Halo LLM endpoint at %s: %s", target_url, err)

        # 2. Handle LLM output or Fallback
        if not raw_reply:
            return self._fallback_handler(
                clean_text,
                child_id,
                child_name,
                child_data,
                session,
                garage_cover_entity=garage_cover_entity,
                home_safe_zone=home_safe_zone,
                context=context,
            )

        # 3. Action Dispatching: parse explicit ACTION directives strictly at start of reply,
        #    while keeping the open-verb regex convenience path for 2B LLM natural-language drift.
        llm_open_garage = bool(re.match(r"^\s*(?:ACTION\s*:\s*)?OPEN_GARAGE\b", raw_reply, re.IGNORECASE))
        llm_spot_plane = bool(re.match(r"^\s*(?:ACTION\s*:\s*)?SPOT_PLANE\b", raw_reply, re.IGNORECASE))

        is_open_garage = llm_open_garage or (
            bool(re.search(r"\b(ouvre|ouvrir|ouvrez|open)\b", clean_text, re.IGNORECASE))
            and not bool(re.search(r"\b(avion|plane|fen[eê]tre|app|application)\b", clean_text, re.IGNORECASE))
        )

        if llm_spot_plane or (not is_open_garage and any(w in clean_text.lower() for w in ("quel est cet avion", "avion passe", "spot plane"))):
            result = self._execute_spot_plane(child_id, child_data, session)
            session.add_turn("user", clean_text)
            session.add_turn("assistant", result)
            return result

        if is_open_garage:
            result = self._execute_open_garage(
                child_data,
                cover_entity=garage_cover_entity,
                home_safe_zone=home_safe_zone,
                context=context,
            )
            session.add_turn("user", clean_text)
            session.add_turn("assistant", result)
            return result

        # Conversational text response
        cleaned_chat = re.sub(
            r"^(ACTION:\s*)?(CHAT:\s*|CHAT\s*)?", "", raw_reply, flags=re.IGNORECASE
        ).strip()
        if len(cleaned_chat) > 138:
            cleaned_chat = cleaned_chat[:135].rsplit(" ", 1)[0] + "…"

        session.add_turn("user", clean_text)
        session.add_turn("assistant", cleaned_chat)
        return cleaned_chat

    def _execute_spot_plane(
        self, child_id: str, child_data: dict[str, Any], session: ChildSession
    ) -> str:
        """Run radar scan, cache flight details in session, and format response."""
        loc_info = resolve_kid_location(self.hass, child_data, child_id)
        user_lat = loc_info["latitude"]
        user_lon = loc_info["longitude"]

        aircraft_list = fetch_live_aircraft_sync(user_lat, user_lon, radius_km=45.0)
        candidates = filter_and_rank_planes(user_lat, user_lon, aircraft_list, max_distance_km=45.0, min_elevation_deg=5.0)

        if not candidates:
            return "🔭 Aucun avion visible au-dessus de toi en ce moment! Regarde à nouveau dans quelques minutes!"

        top_plane = enrich_flight_details(candidates[0], language="fr")
        session.set_spotted_flight(top_plane)

        return format_bounce_response(top_plane, loc_info, language="fr")

    def _is_child_near_home(self, child_data: dict[str, Any], home_safe_zone: str | None = None) -> bool:
        """Return True strictly when child_data shows the child is in the configured Home Safe Zone."""
        target_zone = (home_safe_zone or DEFAULT_HOME_SAFE_ZONE).strip().lower()
        safe_zone = str(
            child_data.get("garmin_safe_zone")
            or child_data.get("active_geofence_name")
            or ""
        ).strip().lower()
        return bool(safe_zone and target_zone and safe_zone == target_zone)

    def _find_garmin_client_and_coordinator(self, child_id: str) -> tuple[Any, Any]:
        """Locate the active GarminJrClient and coordinator for a child."""
        domain_data = getattr(self.hass, "data", {}).get(DOMAIN, {}) if hasattr(self.hass, "data") and isinstance(self.hass.data, dict) else {}
        for item in domain_data.values():
            client = getattr(item, "client", None)
            if client is not None and hasattr(client, "fetch_trackpoints"):
                coord_data = getattr(item, "data", None)
                if not child_id or (isinstance(coord_data, dict) and str(child_id) in coord_data):
                    return client, item
        for item in domain_data.values():
            client = getattr(item, "client", None)
            if client is not None and hasattr(client, "fetch_trackpoints"):
                return client, item
        return None, None

    def _refresh_child_location_live(self, child_data: dict[str, Any]) -> bool:
        """Fetch latest trackpoints live from Garmin Cloud and update child state safely."""
        child_id = str(child_data.get("child_id") or child_data.get("id") or "")
        connect_id = (
            child_data.get("connectId")
            or child_data.get("connect_id")
            or (child_data.get("account") or {}).get("connectId")
        )
        client, coordinator = self._find_garmin_client_and_coordinator(child_id)
        if client is None:
            return False

        try:
            trackpoints = client.fetch_trackpoints(child_id or connect_id, limit=30, connect_id=connect_id)
            if not trackpoints:
                return False

            resolved = resolve_trackpoints_and_safe_zone(
                trackpoints,
                child_data.get("geofences") or [],
                prev_record=child_data,
            )
            updates = {
                key: resolved[key]
                for key in (
                    "latitude",
                    "longitude",
                    "gps_accuracy",
                    "location_timestamp",
                    "fix_type",
                    "garmin_safe_zone",
                    "garmin_geofence_id",
                    "has_wifi",
                    "geofence_latitude",
                    "geofence_longitude",
                    "geofence_radius",
                )
            }
            if resolved.get("garmin_safe_zone"):
                updates["active_geofence_name"] = resolved["garmin_safe_zone"]

            # Update caller's child_data copy for immediate decision making
            child_data.update(updates)

            # Mutate shared coordinator/client caches on the HA event loop cleanly
            def _apply_on_loop() -> None:
                coord_data = getattr(coordinator, "data", None) if coordinator is not None else None
                if isinstance(coord_data, dict) and child_id in coord_data and coord_data[child_id] is not child_data:
                    coord_data[child_id].update(updates)
                client_lock = getattr(client, "_lock", None)
                last_results = getattr(client, "_last_results", None)
                if isinstance(last_results, dict) and child_id in last_results:
                    if client_lock is not None:
                        with client_lock:
                            last_results[child_id].update(updates)
                    else:
                        last_results[child_id].update(updates)
                if coordinator is not None and hasattr(coordinator, "async_update_listeners"):
                    coordinator.async_update_listeners()

            if hasattr(self.hass, "loop") and self.hass.loop.is_running():
                self.hass.loop.call_soon_threadsafe(_apply_on_loop)
            else:
                _apply_on_loop()

            _LOGGER.debug(
                "Garmin Jr: Live cloud check for %s updated safe_zone=%s",
                child_id,
                updates.get("garmin_safe_zone"),
            )
            return True
        except Exception as err:
            _LOGGER.debug("Live location refresh during garage check failed: %s", err)
            return False

    def _execute_open_garage(
        self,
        child_data: dict[str, Any],
        cover_entity: str | None = None,
        home_safe_zone: str | None = None,
        context: Any = None,
    ) -> str:
        """Check configured Home Safe Zone presence (with live cloud trackpoint check) and open garage cover."""
        is_near_home = self._is_child_near_home(child_data, home_safe_zone=home_safe_zone)

        if not is_near_home:
            _LOGGER.debug("Garmin Jr: Cached safe zone != target; fetching live trackpoints before rejecting garage open")
            if self._refresh_child_location_live(child_data):
                is_near_home = self._is_child_near_home(child_data, home_safe_zone=home_safe_zone)

        if not is_near_home:
            return "Tu n'es pas à la maison pour ouvrir le garage! 🏠"

        target_cover = (cover_entity or DEFAULT_GARAGE_COVER_ENTITY).strip() or DEFAULT_GARAGE_COVER_ENTITY

        try:
            # Fire-and-forget on the HA main event loop. blocking=True would wait
            # for the physical door to finish opening (10-30s on most cover
            # integrations), delaying the watch reply until the kid is already
            # through the door. Safety checks are done above, so dispatch the
            # command and reply immediately; log failures via the done callback.
            if not (hasattr(self.hass, "services") and hasattr(self.hass.services, "async_call")):
                raise RuntimeError("Home Assistant service registry unavailable")
            if not (hasattr(self.hass, "loop") and self.hass.loop.is_running()):
                raise RuntimeError("Home Assistant event loop is not running")
            call_kwargs: dict[str, Any] = {
                "target": {"entity_id": target_cover},
                "blocking": False,
            }
            if context is not None:
                call_kwargs["context"] = context

            future = asyncio.run_coroutine_threadsafe(
                self.hass.services.async_call(
                    "cover",
                    "open_cover",
                    **call_kwargs,
                ),
                self.hass.loop,
            )

            def _log_dispatch_result(fut: "concurrent.futures.Future") -> None:
                err = fut.exception()
                if err:
                    _LOGGER.error("cover.open_cover dispatch failed for %s: %s", target_cover, err)
                else:
                    _LOGGER.info("Garmin Jr: Dispatched cover.open_cover for %s", target_cover)

            future.add_done_callback(_log_dispatch_result)
            return "J'ouvre la porte du garage! 🚪 Sois prudent!"
        except Exception as err:
            _LOGGER.error("Failed to open garage door (%s) via service call: %s", target_cover, err)
            return "Erreur lors de l'ouverture du garage! ⚠️"

    def _fallback_handler(
        self,
        text: str,
        child_id: str,
        child_name: str,
        child_data: dict[str, Any],
        session: ChildSession,
        garage_cover_entity: str | None = None,
        home_safe_zone: str | None = None,
        context: Any = None,
    ) -> str:
        """Rule-based fallback when inference server is offline."""
        lower = text.lower()
        if any(w in lower for w in ("avion", "plane", "vole", "ciel", "vol")):
            return self._execute_spot_plane(child_id, child_data, session)
        if re.search(r"\b(garage|porte|ouvre|ouvrir|ouvrez|open)\b", lower):
            return self._execute_open_garage(
                child_data,
                cover_entity=garage_cover_entity,
                home_safe_zone=home_safe_zone,
                context=context,
            )
        return "Message bien reçu! 👍"
