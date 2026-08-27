"""Plane Spotter engine for Garmin Jr integration."""
from __future__ import annotations

import logging
import math
import time
from typing import Any

import requests

_LOGGER = logging.getLogger(__name__)

# Primary ADS-B radar endpoints
OPENSKY_API_URL = "https://opensky-network.org/api/states/all"
AIRPLANES_LIVE_URL = "https://api.airplanes.live/v2/point"

# Common Airline ICAO 3-letter prefix mapping
AIRLINE_MAPPING: dict[str, str] = {
    "ACA": "Air Canada",
    "ROU": "Air Canada Rouge",
    "TSC": "Air Transat",
    "WJA": "WestJet",
    "WEN": "WestJet Encore",
    "POE": "Porter Airlines",
    "PNO": "PAL Aerospace",
    "PVL": "PAL Airlines",
    "SWG": "Sunwing",
    "JZA": "Jazz Aviation",
    "GGN": "Air Georgian",
    "FLE": "Flair Airlines",
    "CJT": "Cargojet",
    "DAL": "Delta Air Lines",
    "AAL": "American Airlines",
    "UAL": "United Airlines",
    "SWA": "Southwest Airlines",
    "FDX": "FedEx",
    "UPS": "UPS Airlines",
    "AFR": "Air France",
    "BAW": "British Airways",
    "KLM": "KLM",
    "DLH": "Lufthansa",
    "VIR": "Virgin Atlantic",
    "EIN": "Aer Lingus",
    "IBE": "Iberia",
    "AZA": "ITA Airways",
    "QTR": "Qatar Airways",
    "UAE": "Emirates",
    "CFC": "Aviation Royale Canadienne (RCAF)",
}

# Aircraft ICAO Type Code mapping to kid-friendly model names
AIRCRAFT_TYPE_MAPPING: dict[str, str] = {
    "B788": "Boeing 787-8 Dreamliner",
    "B789": "Boeing 787-9 Dreamliner",
    "B78X": "Boeing 787-10 Dreamliner",
    "B772": "Boeing 777-200",
    "B77L": "Boeing 777-200LR",
    "B77W": "Boeing 777-300ER",
    "B77F": "Boeing 777 Cargo",
    "B763": "Boeing 767-300",
    "B764": "Boeing 767-400",
    "B752": "Boeing 757-200",
    "B737": "Boeing 737-700",
    "B738": "Boeing 737-800",
    "B739": "Boeing 737-900",
    "B38M": "Boeing 737 MAX 8",
    "B39M": "Boeing 737 MAX 9",
    "B744": "Boeing 747-400",
    "B748": "Boeing 747-8 Intercontinental",
    "A388": "Airbus A380",
    "A359": "Airbus A350-900",
    "A35K": "Airbus A350-1000",
    "A343": "Airbus A340-300",
    "A332": "Airbus A330-200",
    "A333": "Airbus A330-300",
    "A339": "Airbus A330-900neo",
    "A321": "Airbus A321",
    "A21N": "Airbus A321neo",
    "A320": "Airbus A320",
    "A20N": "Airbus A320neo",
    "A319": "Airbus A319",
    "BCS3": "Airbus A220-300",
    "A223": "Airbus A220-300",
    "BCS1": "Airbus A220-100",
    "A221": "Airbus A220-100",
    "DH8D": "Dash 8-Q400",
    "DH8C": "Dash 8-300",
    "DH8A": "Dash 8-100",
    "AT76": "ATR 72-600",
    "E190": "Embraer E190",
    "E195": "Embraer E195",
    "E75L": "Embraer E175",
    "CRJ9": "Bombardier CRJ-900",
    "CRJ7": "Bombardier CRJ-700",
    "CRJ2": "Bombardier CRJ-200",
    "CL35": "Bombardier Challenger 350",
    "CL60": "Bombardier Challenger 600",
    "GL5T": "Bombardier Global 5000",
    "GLEX": "Bombardier Global Express",
    "PC12": "Pilatus PC-12",
    "PC24": "Pilatus PC-24",
    "C172": "Cessna 172 Skyhawk",
    "C182": "Cessna 182 Skylane",
    "C208": "Cessna Caravan",
    "PA28": "Piper Cherokee",
    "C17": "Boeing C-17 Globemaster III",
    "C130": "Lockheed C-130 Hercules",
    "CC130": "Lockheed CC-130 Hercules",
    "A400": "Airbus A400M Atlas",
}


def haversine_bearing_elevation(
    lat1: float, lon1: float, lat2: float, lon2: float, alt_m: float
) -> tuple[float, float, float]:
    """Calculate ground distance (meters), compass bearing (deg), and elevation angle (deg)."""
    R = 6371000.0  # Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    dist_m = R * c

    # Compass Bearing
    y = math.sin(delta_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda)
    bearing = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0

    # Elevation Angle (above horizon)
    if dist_m > 0:
        elevation_deg = math.degrees(math.atan2(alt_m, dist_m))
    else:
        elevation_deg = 90.0

    return dist_m, bearing, elevation_deg


def bearing_to_cardinal(deg: float, language: str = "fr") -> str:
    """Convert bearing angle in degrees to a friendly cardinal direction."""
    if language == "fr":
        dirs = [
            "Nord",
            "Nord-Est",
            "Est",
            "Sud-Est",
            "Sud",
            "Sud-Ouest",
            "Ouest",
            "Nord-Ouest",
        ]
    else:
        dirs = [
            "North",
            "North-East",
            "East",
            "South-East",
            "South",
            "South-West",
            "West",
            "North-West",
        ]
    ix = round(deg / 45.0) % 8
    return dirs[ix]


def resolve_kid_location(
    hass: Any, kid_data: dict[str, Any], kid_id: str
) -> dict[str, Any]:
    """Resolve child location using 3-tier priority: HA Zone > Garmin Geofence > Watch GPS."""
    # 1. Check if matched to a known Home Assistant Zone
    matched_zone_name = kid_data.get("matched_ha_zone")
    if matched_zone_name:
        for state in hass.states.async_all("zone"):
            if state.name.lower() == matched_zone_name.lower() or state.entity_id.lower() == f"zone.{matched_zone_name.lower()}":
                lat = state.attributes.get("latitude")
                lon = state.attributes.get("longitude")
                if lat is not None and lon is not None:
                    return {
                        "latitude": float(lat),
                        "longitude": float(lon),
                        "source": "ha_zone",
                        "zone_name": state.name,
                        "stale": False,
                    }

    # 2. Check if inside an active Garmin Safe Zone / Geofence
    active_gf_name = kid_data.get("active_geofence_name")
    gf_lat = kid_data.get("geofence_lat")
    gf_lon = kid_data.get("geofence_lon")
    if active_gf_name and gf_lat is not None and gf_lon is not None:
        return {
            "latitude": float(gf_lat),
            "longitude": float(gf_lon),
            "source": "garmin_geofence",
            "zone_name": active_gf_name,
            "stale": False,
        }

    # 3. Fallback: Last known watch GPS trackpoint
    gps_lat = kid_data.get("latitude")
    gps_lon = kid_data.get("longitude")
    loc_ts = kid_data.get("location_timestamp") or kid_data.get("last_sync")

    age_seconds = 0
    stale = False
    if loc_ts:
        try:
            import datetime
            if isinstance(loc_ts, str):
                cleaned_ts = loc_ts.replace("Z", "+00:00")
                dt = datetime.datetime.fromisoformat(cleaned_ts)
                now_utc = datetime.datetime.now(datetime.timezone.utc)
                age_seconds = (now_utc - dt).total_seconds()
                if age_seconds > 1800:  # > 30 mins
                    stale = True
        except Exception:
            pass

    if gps_lat is not None and gps_lon is not None:
        return {
            "latitude": float(gps_lat),
            "longitude": float(gps_lon),
            "source": "watch_gps",
            "zone_name": "Extérieur",
            "stale": stale,
            "age_seconds": age_seconds,
        }

    # Fallback: Home Assistant default location
    return {
        "latitude": hass.config.latitude,
        "longitude": hass.config.longitude,
        "source": "home_default",
        "zone_name": "Maison",
        "stale": False,
    }


def fetch_live_aircraft_sync(
    lat: float, lon: float, radius_km: float = 35.0
) -> list[dict[str, Any]]:
    """Fetch live aircraft states around a coordinate from OpenSky Network or Airplanes.live."""
    delta_lat = radius_km / 111.0
    delta_lon = radius_km / (111.0 * max(0.2, math.cos(math.radians(lat))))

    lamin = lat - delta_lat
    lamax = lat + delta_lat
    lomin = lon - delta_lon
    lomax = lon + delta_lon

    # Try OpenSky Network first
    try:
        url = f"{OPENSKY_API_URL}?lamin={lamin:.4f}&lomin={lomin:.4f}&lamax={lamax:.4f}&lomax={lomax:.4f}"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            states = data.get("states") or []
            aircraft = []
            for s in states:
                callsign = (s[1] or "").strip()
                s_lon = s[5]
                s_lat = s[6]
                s_alt = s[7] or s[13] or 0.0
                on_ground = s[8]
                velocity = s[9] or 0.0
                track = s[10] or 0.0

                if s_lat is not None and s_lon is not None and not on_ground and s_alt > 50:
                    aircraft.append({
                        "icao24": s[0],
                        "callsign": callsign,
                        "origin_country": s[2],
                        "latitude": float(s_lat),
                        "longitude": float(s_lon),
                        "altitude_m": float(s_alt),
                        "altitude_ft": float(s_alt) * 3.28084,
                        "speed_kmh": float(velocity) * 3.6,
                        "track": float(track),
                        "source": "opensky",
                    })
            if aircraft:
                return aircraft
    except Exception as err:
        _LOGGER.debug("OpenSky query error: %s", err)

    # Fallback to Airplanes.live
    try:
        nm_radius = int(radius_km * 0.539957)
        url = f"{AIRPLANES_LIVE_URL}/{lat:.4f}/{lon:.4f}/{nm_radius}"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            ac_list = data.get("ac") or []
            aircraft = []
            for a in ac_list:
                callsign = (a.get("flight") or "").strip()
                a_lat = a.get("lat")
                a_lon = a.get("lon")
                a_alt_ft = a.get("alt_baro") or a.get("alt_geom") or 0
                if a_alt_ft == "ground":
                    continue
                a_alt_m = float(a_alt_ft) * 0.3048 if isinstance(a_alt_ft, (int, float)) else 0.0

                if a_lat is not None and a_lon is not None and a_alt_m > 50:
                    aircraft.append({
                        "icao24": a.get("hex"),
                        "callsign": callsign,
                        "type_code": a.get("t"),
                        "registration": a.get("r"),
                        "origin_country": "Unknown",
                        "latitude": float(a_lat),
                        "longitude": float(a_lon),
                        "altitude_m": a_alt_m,
                        "altitude_ft": float(a_alt_ft) if isinstance(a_alt_ft, (int, float)) else 0.0,
                        "speed_kmh": float(a.get("gs", 0)) * 1.852,
                        "track": float(a.get("track", 0)),
                        "source": "airplanes_live",
                    })
            if aircraft:
                return aircraft
    except Exception as err:
        _LOGGER.debug("Airplanes.live fallback query error: %s", err)

    return []


def filter_and_rank_planes(
    user_lat: float,
    user_lon: float,
    aircraft_list: list[dict[str, Any]],
    max_distance_km: float = 30.0,
    min_elevation_deg: float = 12.0,
) -> list[dict[str, Any]]:
    """Filter planes within visible sightline and rank by elevation angle and proximity."""
    candidates = []
    for ac in aircraft_list:
        dist_m, bearing, elevation_deg = haversine_bearing_elevation(
            user_lat, user_lon, ac["latitude"], ac["longitude"], ac["altitude_m"]
        )
        dist_km = dist_m / 1000.0

        if dist_km <= max_distance_km:
            if elevation_deg >= min_elevation_deg or (dist_km <= 3.0 and elevation_deg >= 8.0):
                ac_copy = dict(ac)
                ac_copy["distance_km"] = round(dist_km, 1)
                ac_copy["bearing_deg"] = round(bearing, 1)
                ac_copy["elevation_deg"] = round(elevation_deg, 1)
                ac_copy["slant_range_km"] = round(math.sqrt(dist_km**2 + (ac["altitude_m"]/1000.0)**2), 1)
                candidates.append(ac_copy)

    candidates.sort(key=lambda x: (-x["elevation_deg"], x["distance_km"]))
    return candidates


def enrich_flight_details(aircraft: dict[str, Any], language: str = "fr") -> dict[str, Any]:
    """Enrich flight with airline name, model name, and cardinal directions."""
    callsign = aircraft.get("callsign", "")
    type_code = aircraft.get("type_code", "")

    # 1. Resolve Airline from Callsign prefix (3 chars)
    airline = "Privé / Inconnu" if language == "fr" else "Private / Unknown"
    if len(callsign) >= 3:
        prefix = callsign[:3].upper()
        if prefix in AIRLINE_MAPPING:
            airline = AIRLINE_MAPPING[prefix]
        elif callsign.startswith("C-") or callsign.startswith("CF-"):
            airline = "Avion civil canadien" if language == "fr" else "Canadian Civil Aircraft"
        elif callsign.startswith("N"):
            airline = "Avion civil américain" if language == "fr" else "US Civil Aircraft"

    # 2. Resolve Aircraft Type
    model_name = AIRCRAFT_TYPE_MAPPING.get(type_code.upper(), "") if type_code else ""
    if not model_name:
        if aircraft.get("altitude_ft", 0) > 25000:
            model_name = "Avion de ligne (Gros porteur)" if language == "fr" else "Commercial Jetliner"
        elif aircraft.get("altitude_ft", 0) > 10000:
            model_name = "Avion régional / Jet" if language == "fr" else "Regional Jet"
        else:
            model_name = "Avion léger / Hélice" if language == "fr" else "Light Aircraft"

    cardinal = bearing_to_cardinal(aircraft.get("bearing_deg", 0.0), language=language)

    enriched = dict(aircraft)
    enriched["airline"] = airline
    enriched["model_name"] = model_name
    enriched["cardinal_direction"] = cardinal

    return enriched


def format_bounce_response(
    flight_data: dict[str, Any] | None,
    location_info: dict[str, Any],
    language: str = "fr",
) -> str:
    """Format kid-friendly response under 140 chars for Garmin Bounce watch."""
    if not flight_data:
        if language == "fr":
            return "🔭 Aucun avion visible au-dessus de toi en ce moment! Regarde à nouveau dans quelques minutes!"
        return "🔭 No planes visible directly overhead right now! Check back in a few minutes!"

    callsign = flight_data.get("callsign") or flight_data.get("icao24", "Inconnu")
    airline = flight_data.get("airline", "")
    model = flight_data.get("model_name", "")
    cardinal = flight_data.get("cardinal_direction", "")
    elevation = int(round(flight_data.get("elevation_deg", 0)))
    alt_ft = int(round(flight_data.get("altitude_ft", 0)))

    if language == "fr":
        elev_desc = "très haut" if elevation >= 45 else ("haut" if elevation >= 25 else "au loin")
        lines = [
            f"✈️ {airline} ({callsign})",
            f"🧭 Regarde vers le {cardinal} ({elev_desc}, {elevation}°)",
            f"🛩️ {model}",
            f"☁️ {alt_ft:,} pi".replace(",", " "),
        ]
    else:
        elev_desc = "straight up" if elevation >= 45 else ("high" if elevation >= 25 else "low")
        lines = [
            f"✈️ {airline} ({callsign})",
            f"🧭 Look {cardinal} ({elev_desc}, {elevation}°)",
            f"🛩️ {model}",
            f"☁️ {alt_ft:,} ft",
        ]

    return "\n".join(lines)
