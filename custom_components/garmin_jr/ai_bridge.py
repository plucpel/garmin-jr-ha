"""AI Bridge for Garmin Bounce watches powered by local Strix Halo NPU."""
from __future__ import annotations

import datetime
import json
import logging
import re
import time
from typing import Any

import requests

from .const import DOMAIN, LOGGER
from .plane_spotter import (
    enrich_flight_details,
    fetch_live_aircraft_sync,
    filter_and_rank_planes,
    format_bounce_response,
    resolve_kid_location,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_STRIX_HALO_URL = "http://192.168.50.6:13305"
DEFAULT_MODEL = "gemma-4-E2B-QAT-MTP"
MAX_HISTORY_TURNS = 6
FLIGHT_CONTEXT_TTL_SECONDS = 1200  # 20 minutes


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
        airline = f.get("airline") or "Inconnu"
        callsign = f.get("callsign_iata") or f.get("callsign") or ""
        model = f.get("model_name") or "Avion de ligne"
        route = f.get("route") or "Vol privé / nolisé (sans horaire public)"
        alt_ft = int(round(f.get("altitude_ft", 0)))
        speed_kmh = int(round(f.get("speed_kmh", 0)))

        ctx = f"Dernier avion repéré : {airline} ({callsign}), modèle {model}, trajet {route}, altitude {alt_ft:,} pi, vitesse {speed_kmh} km/h."
        return ctx.replace(",", " ")


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
            f"{child_name} est passionné et curieux : il aime les explications précises, les chiffres réels et les détails techniques (vitesse en km/h, capacité passagers, altitude, moteurs, principes physiques comme les traînées de condensation), tout en restant court et clair.",
            "",
            "Contraintes strictes :",
            "- Réponds TOUJOURS en français.",
            "- Longueur maximale : MOINS DE 140 CARACTÈRES par message (écran de montre).",
            "- Donne des chiffres et faits précis quand demandé (ex: ~220 passagers, ~870 km/h, 35 000 pi).",
            "",
            "Actions disponibles :",
            "1. Si Benjamin demande quel avion passe ou veut repérer un avion dans le ciel :",
            "ACTION: SPOT_PLANE",
            "",
            "2. Si Benjamin demande d'ouvrir la porte du garage :",
            "ACTION: OPEN_GARAGE",
            "",
            "3. Pour toute question, discussion ou suivi technique :",
            "ACTION: CHAT <ta réponse courte de moins de 140 caractères>",
            "",
            f"Lieu actuel de {child_name} : {safe_zone}.",
        ]

        if flight_ctx:
            prompt_lines.append(f"Contexte de vol actuel : {flight_ctx}")

        return "\n".join(prompt_lines)

    def process_incoming_message(
        self,
        child_id: str,
        child_name: str,
        incoming_text: str,
        child_data: dict[str, Any],
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

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 400,
        }

        # 1. Query Strix Halo NPU with timeout
        raw_reply = None
        try:
            url = f"{self.base_url}/v1/chat/completions"
            resp = requests.post(url, json=payload, timeout=6.0)
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
            _LOGGER.warning("Could not reach Strix Halo LLM endpoint: %s", err)

        # 2. Handle LLM output or Fallback
        if not raw_reply:
            return self._fallback_handler(clean_text, child_id, child_name, child_data, session)

        # 3. Action Dispatching
        upper_reply = raw_reply.upper()
        if "SPOT_PLANE" in upper_reply:
            result = self._execute_spot_plane(child_id, child_data, session)
            session.add_turn("user", clean_text)
            session.add_turn("assistant", result)
            return result

        if "OPEN_GARAGE" in upper_reply:
            result = self._execute_open_garage(child_data)
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

    def _execute_open_garage(self, child_data: dict[str, Any]) -> str:
        """Check safe zone presence and trigger garage door opening."""
        # Safe zone check: Papa / Home / zone.home
        safe_zone = str(
            child_data.get("active_geofence_name")
            or child_data.get("garmin_safe_zone")
            or child_data.get("matched_ha_zone")
            or ""
        ).lower()

        is_near_home = (
            "papa" in safe_zone
            or "home" in safe_zone
            or "maison" in safe_zone
            or self.hass.states.is_state("device_tracker.benjamin_benjamin_location", "home")
            or self.hass.states.is_state("device_tracker.benjamin_benjamin_location", "Papa")
        )

        if not is_near_home:
            return "Tu n'es pas à la maison pour ouvrir le garage! 🏠"

        try:
            self.hass.services.call("cover", "open_cover", {"entity_id": "cover.garage_door"}, blocking=False)
            return "J'ouvre la porte du garage! 🚪 Sois prudent!"
        except Exception as err:
            _LOGGER.error("Failed to open garage door via service call: %s", err)
            return "Erreur lors de l'ouverture du garage! ⚠️"

    def _fallback_handler(
        self,
        text: str,
        child_id: str,
        child_name: str,
        child_data: dict[str, Any],
        session: ChildSession,
    ) -> str:
        """Rule-based fallback when Strix Halo inference server is offline."""
        lower = text.lower()
        if "avion" in lower or "plane" in lower or "vole" in lower:
            return self._execute_spot_plane(child_id, child_data, session)
        if "garage" in lower or "porte" in lower:
            return self._execute_open_garage(child_data)
        return "Message bien reçu! 👍"
