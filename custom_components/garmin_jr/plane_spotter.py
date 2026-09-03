"""Plane Spotter engine for Garmin Jr integration."""
from __future__ import annotations

import logging
import math
import time
from typing import Any

import requests

_LOGGER = logging.getLogger(__name__)

# Primary ADS-B radar endpoints
ADSB_LOL_API_URL = "https://api.adsb.lol/v2/point"
AIRPLANES_LIVE_URL = "https://api.airplanes.live/v2/point"
OPENSKY_API_URL = "https://opensky-network.org/api/states/all"

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
    "B753": "Boeing 757-300",
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
    "E75S": "Embraer E175",
    "E290": "Embraer E190-E2",
    "E295": "Embraer E195-E2",
    "E170": "Embraer E170",
    "E145": "Embraer ERJ-145",
    "CRJX": "Bombardier CRJ-1000",
    "CRJ9": "Bombardier CRJ-900",
    "CRJ7": "Bombardier CRJ-700",
    "CRJ2": "Bombardier CRJ-200",
    "CRJ1": "Bombardier CRJ-100",
    "CL30": "Bombardier Challenger 300",
    "CL35": "Bombardier Challenger 350",
    "CL60": "Bombardier Challenger 600",
    "CL65": "Bombardier Challenger 650",
    "GL5T": "Bombardier Global 5000",
    "GL6T": "Bombardier Global 6000",
    "GL7T": "Bombardier Global 7500",
    "GLEX": "Bombardier Global Express",
    "PC12": "Pilatus PC-12",
    "PC24": "Pilatus PC-24",
    "C172": "Cessna 172 Skyhawk",
    "C182": "Cessna 182 Skylane",
    "C208": "Cessna Caravan",
    "C510": "Cessna Citation Mustang",
    "C525": "Cessna CitationJet",
    "C560": "Cessna Citation Excel",
    "C680": "Cessna Citation Sovereign",
    "PA28": "Piper Cherokee",
    "PA34": "Piper Seneca",
    "PA31": "Piper Navajo",
    "BE20": "Beechcraft King Air 200",
    "B350": "Beechcraft King Air 350",
    "BE36": "Beechcraft Bonanza",
    "BE58": "Beechcraft Baron",
    "DA40": "Diamond DA40 Star",
    "DA42": "Diamond DA42 Twin Star",
    "DA62": "Diamond DA62",
    "SR20": "Cirrus SR20",
    "SR22": "Cirrus SR22",
    "SF50": "Cirrus Vision Jet",
    "GLF4": "Gulfstream IV",
    "GLF5": "Gulfstream V",
    "GLF6": "Gulfstream G650",
    "FA7X": "Dassault Falcon 7X",
    "FA8X": "Dassault Falcon 8X",
    "F2TH": "Dassault Falcon 2000",
    "C17": "Boeing C-17 Globemaster III",
    "C130": "Lockheed C-130 Hercules",
    "CC130": "Lockheed CC-130 Hercules",
    "A400": "Airbus A400M Atlas",
    "B06": "Bell 206 JetRanger",
    "B429": "Bell 429",
    "EC35": "Eurocopter EC135",
    "EC45": "Eurocopter EC145",
}

# French translation for common international origin/destination cities
FRENCH_CITY_TRANSLATIONS: dict[str, str] = {
    "London": "Londres",
    "Rome": "Rome",
    "Paris": "Paris",
    "Athens": "Athènes",
    "Vienna": "Vienne",
    "Brussels": "Bruxelles",
    "Geneva": "Genève",
    "Lisbon": "Lisbonne",
    "Munich": "Munich",
    "Warsaw": "Varsovie",
    "Copenhagen": "Copenhague",
    "Frankfurt": "Francfort",
    "Milan": "Milan",
    "Venice": "Venise",
    "Florence": "Florence",
    "Naples": "Naples",
    "Seville": "Séville",
    "Edinburgh": "Édimbourg",
    "Dublin": "Dublin",
    "Tokyo": "Tokyo",
    "Beijing": "Pékin",
    "Algiers": "Alger",
    "Cairo": "Le Caire",
    "Doha": "Doha",
    "Dubai": "Dubaï",
    "Beirut": "Beyrouth",
    "Havana": "La Havane",
}


def translate_city(city: str, language: str = "fr") -> str:
    """Translate common city names to French if applicable."""
    if not city or language != "fr":
        return city
    return FRENCH_CITY_TRANSLATIONS.get(city, city)


def fetch_flight_route(callsign: str, language: str = "fr") -> dict[str, Any]:
    """Fetch flight route (origin and destination) and airline from ADS-B DB."""
    if not callsign or len(callsign) < 3:
        return {}
    clean_callsign = callsign.strip().upper()
    try:
        url = f"https://api.adsbdb.com/v0/callsign/{clean_callsign}"
        resp = requests.get(url, timeout=2.5)
        if resp.status_code == 200:
            data = resp.json().get("response", {}).get("flightroute", {})
            orig = data.get("origin", {})
            dest = data.get("destination", {})
            airline = data.get("airline", {})
            orig_city = orig.get("municipality") or orig.get("name")
            dest_city = dest.get("municipality") or dest.get("name")
            if orig_city and dest_city:
                orig_fr = translate_city(orig_city, language=language)
                dest_fr = translate_city(dest_city, language=language)
                orig_iata = orig.get("iata_code")
                dest_iata = dest.get("iata_code")
                return {
                    "origin_city": orig_fr,
                    "origin_iata": orig_iata,
                    "destination_city": dest_fr,
                    "destination_iata": dest_iata,
                    "airline_name": airline.get("name"),
                    "callsign_iata": data.get("callsign_iata"),
                }
    except Exception as err:
        _LOGGER.debug("Could not fetch flight route for %s: %s", clean_callsign, err)
    return {}


def fetch_aircraft_details(icao24: str) -> dict[str, Any]:
    """Fetch aircraft make and model from ADS-B hex databases."""
    if not icao24:
        return {}
    clean_hex = icao24.strip().lower()

    # 1. Query adsb.lol hex endpoint
    try:
        url = f"https://api.adsb.lol/v2/hex/{clean_hex}"
        resp = requests.get(url, timeout=2.0)
        if resp.status_code == 200:
            ac_list = resp.json().get("ac") or []
            if ac_list:
                ac = ac_list[0]
                t_code = ac.get("t")
                desc = ac.get("desc")
                r_reg = ac.get("r")
                own_op = ac.get("ownOp")
                resolved_type = None
                if t_code and t_code.upper() in AIRCRAFT_TYPE_MAPPING:
                    resolved_type = AIRCRAFT_TYPE_MAPPING[t_code.upper()]
                elif desc:
                    resolved_type = desc.title()
                if resolved_type or t_code:
                    return {
                        "type": resolved_type or t_code,
                        "icao_type": t_code,
                        "registration": r_reg,
                        "operator": own_op,
                    }
    except Exception as err:
        _LOGGER.debug("adsb.lol hex lookup error for %s: %s", clean_hex, err)

    # 2. Query opendata.adsb.fi hex endpoint
    try:
        url = f"https://opendata.adsb.fi/api/v2/hex/{clean_hex}"
        resp = requests.get(url, timeout=2.0)
        if resp.status_code == 200:
            ac_list = resp.json().get("ac") or []
            if ac_list:
                ac = ac_list[0]
                t_code = ac.get("t")
                desc = ac.get("desc")
                r_reg = ac.get("r")
                resolved_type = None
                if t_code and t_code.upper() in AIRCRAFT_TYPE_MAPPING:
                    resolved_type = AIRCRAFT_TYPE_MAPPING[t_code.upper()]
                elif desc:
                    resolved_type = desc.title()
                if resolved_type or t_code:
                    return {
                        "type": resolved_type or t_code,
                        "icao_type": t_code,
                        "registration": r_reg,
                    }
    except Exception as err:
        _LOGGER.debug("adsb.fi hex lookup error for %s: %s", clean_hex, err)

    # 3. Query adsbdb.com
    try:
        url = f"https://api.adsbdb.com/v0/aircraft/{clean_hex}"
        resp = requests.get(url, timeout=2.0)
        if resp.status_code == 200:
            data = resp.json().get("response", {}).get("aircraft", {})
            mfg = data.get("manufacturer")
            m_type = data.get("type")
            t_code = data.get("icao_type")
            resolved = None
            if t_code and t_code.upper() in AIRCRAFT_TYPE_MAPPING:
                resolved = AIRCRAFT_TYPE_MAPPING[t_code.upper()]
            elif mfg and m_type:
                resolved = f"{mfg} {m_type}".strip()
            elif m_type:
                resolved = m_type.strip()
            return {
                "manufacturer": mfg,
                "type": resolved or m_type,
                "icao_type": t_code,
            }
    except Exception as err:
        _LOGGER.debug("adsbdb aircraft lookup error for %s: %s", clean_hex, err)

    return {}


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
    lat: float, lon: float, radius_km: float = 45.0
) -> list[dict[str, Any]]:
    """Fetch live aircraft states around coordinates prioritizing fast ADS-B feeds."""
    aircraft: list[dict[str, Any]] = []

    # 1. Try adsb.lol first (open, returns exact ICAO type codes, speeds, altitudes, registrations)
    try:
        nm_radius = max(5, int(radius_km * 0.539957))
        url = f"{ADSB_LOL_API_URL}/{lat:.4f}/{lon:.4f}/{nm_radius}"
        resp = requests.get(url, timeout=4)
        if resp.status_code == 200:
            data = resp.json()
            ac_list = data.get("ac") or []
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
                        "source": "adsb_lol",
                    })
            if aircraft:
                return aircraft
    except Exception as err:
        _LOGGER.debug("adsb.lol query error: %s", err)

    # 2. Fallback to Airplanes.live
    try:
        nm_radius = max(5, int(radius_km * 0.539957))
        url = f"{AIRPLANES_LIVE_URL}/{lat:.4f}/{lon:.4f}/{nm_radius}"
        resp = requests.get(url, timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            ac_list = data.get("ac") or []
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
        _LOGGER.debug("Airplanes.live query error: %s", err)

    # 3. Fallback to OpenSky Network
    delta_lat = radius_km / 111.0
    delta_lon = radius_km / (111.0 * max(0.2, math.cos(math.radians(lat))))
    lamin = lat - delta_lat
    lamax = lat + delta_lat
    lomin = lon - delta_lon
    lomax = lon + delta_lon

    try:
        url = f"{OPENSKY_API_URL}?lamin={lamin:.4f}&lomin={lomin:.4f}&lamax={lamax:.4f}&lomax={lomax:.4f}"
        resp = requests.get(url, timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            states = data.get("states") or []
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
        _LOGGER.debug("OpenSky fallback query error: %s", err)

    return []


def filter_and_rank_planes(
    user_lat: float,
    user_lon: float,
    aircraft_list: list[dict[str, Any]],
    max_distance_km: float = 45.0,
    min_elevation_deg: float = 5.0,
) -> list[dict[str, Any]]:
    """Filter planes within visible sightline and rank by visual prominence and proximity."""
    candidates = []
    for ac in aircraft_list:
        dist_m, bearing, elevation_deg = haversine_bearing_elevation(
            user_lat, user_lon, ac["latitude"], ac["longitude"], ac["altitude_m"]
        )
        dist_km = dist_m / 1000.0

        if dist_km <= max_distance_km and elevation_deg >= min_elevation_deg:
            ac_copy = dict(ac)
            ac_copy["distance_km"] = round(dist_km, 1)
            ac_copy["bearing_deg"] = round(bearing, 1)
            ac_copy["elevation_deg"] = round(elevation_deg, 1)
            ac_copy["slant_range_km"] = round(math.sqrt(dist_km**2 + (ac["altitude_m"] / 1000.0)**2), 1)
            # Prominence score: higher elevation and closer distance score highest
            prominence = (elevation_deg * 2.0) + (ac["altitude_m"] / 1000.0) / max(1.0, dist_km) * 10.0
            ac_copy["prominence"] = prominence
            candidates.append(ac_copy)

    candidates.sort(key=lambda x: (-x["elevation_deg"], x["distance_km"]))
    return candidates


def enrich_flight_details(aircraft: dict[str, Any], language: str = "fr") -> dict[str, Any]:
    """Enrich flight with route (origin/destination), airline, make/model, and cardinal directions."""
    callsign = aircraft.get("callsign", "")
    type_code = aircraft.get("type_code", "")
    icao24 = aircraft.get("icao24", "")

    # 1. Fetch live route information (Origin ➔ Destination)
    route_info = fetch_flight_route(callsign, language=language) if callsign else {}

    # 2. Resolve Airline
    airline = None
    if route_info.get("airline_name"):
        airline = route_info["airline_name"]
    elif len(callsign) >= 3:
        prefix = callsign[:3].upper()
        if prefix in AIRLINE_MAPPING:
            airline = AIRLINE_MAPPING[prefix]
        elif callsign.startswith("C-") or callsign.startswith("CF-"):
            airline = "Avion civil canadien" if language == "fr" else "Canadian Civil Aircraft"
        elif callsign.startswith("N"):
            airline = "Avion civil américain" if language == "fr" else "US Civil Aircraft"

    if not airline:
        airline = "Privé / Inconnu" if language == "fr" else "Private / Unknown"

    # 3. Resolve Specific Make / Model
    model_name = AIRCRAFT_TYPE_MAPPING.get(type_code.upper(), "") if type_code else ""
    if not model_name and icao24:
        ac_details = fetch_aircraft_details(icao24)
        mfg = ac_details.get("manufacturer")
        m_type = ac_details.get("type")
        t_from_hex = ac_details.get("icao_type")
        if t_from_hex and t_from_hex.upper() in AIRCRAFT_TYPE_MAPPING:
            model_name = AIRCRAFT_TYPE_MAPPING[t_from_hex.upper()]
        elif mfg and m_type:
            model_name = f"{mfg} {m_type}".strip()
        elif m_type:
            model_name = m_type.strip()

    if not model_name and type_code:
        tc = type_code.upper()
        if tc.startswith("A") and len(tc) == 4 and tc[1:].isdigit():
            model_name = f"Airbus A{tc[1:]}"
        elif tc.startswith("B") and len(tc) == 4 and tc[1:].isdigit():
            model_name = f"Boeing {tc[1:]}"
        elif tc.startswith("E") and len(tc) in (4, 5):
            model_name = f"Embraer {tc}"
        elif tc.startswith("CRJ"):
            model_name = f"Bombardier {tc}"
        else:
            model_name = f"Modèle {tc}"

    if not model_name:
        if aircraft.get("altitude_ft", 0) > 25000:
            model_name = "Avion de ligne" if language == "fr" else "Commercial Jetliner"
        elif aircraft.get("altitude_ft", 0) > 10000:
            model_name = "Avion régional" if language == "fr" else "Regional Aircraft"
        else:
            model_name = "Avion léger" if language == "fr" else "Light Aircraft"

    # 4. Format Route
    route_str = None
    if route_info.get("origin_city") and route_info.get("destination_city"):
        orig = route_info["origin_city"]
        dest = route_info["destination_city"]
        orig_iata = route_info.get("origin_iata")
        dest_iata = route_info.get("destination_iata")
        if orig_iata and dest_iata:
            route_str = f"{orig} ({orig_iata}) ➔ {dest} ({dest_iata})"
        else:
            route_str = f"{orig} ➔ {dest}"

    cardinal = bearing_to_cardinal(aircraft.get("bearing_deg", 0.0), language=language)

    enriched = dict(aircraft)
    enriched["airline"] = airline
    enriched["model_name"] = model_name
    enriched["route"] = route_str
    enriched["route_info"] = route_info
    enriched["callsign_iata"] = route_info.get("callsign_iata")
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

    callsign = flight_data.get("callsign_iata") or flight_data.get("callsign") or flight_data.get("icao24", "Inconnu")
    airline = flight_data.get("airline", "")
    model = flight_data.get("model_name", "")
    route = flight_data.get("route")
    cardinal = flight_data.get("cardinal_direction", "")
    elevation = int(round(flight_data.get("elevation_deg", 0)))
    alt_ft = int(round(flight_data.get("altitude_ft", 0)))

    if language == "fr":
        elev_desc = "très haut" if elevation >= 45 else ("haut" if elevation >= 25 else "au loin")
        alt_str = f"{alt_ft:,} pi".replace(",", " ")
        lines = [f"✈️ {airline} ({callsign})"]
        if route:
            lines.append(f"🛫 {route}")
        else:
            lines.append("🛫 Vol privé / nolisé")
        if model:
            lines.append(f"🛩️ {model}")
        lines.append(f"🧭 {cardinal} ({elev_desc}, {elevation}°) • {alt_str}")
    else:
        elev_desc = "straight up" if elevation >= 45 else ("high" if elevation >= 25 else "low")
        lines = [f"✈️ {airline} ({callsign})"]
        if route:
            lines.append(f"🛫 {route}")
        else:
            lines.append("🛫 Private / Chartered flight")
        if model:
            lines.append(f"🛩️ {model}")
        lines.append(f"🧭 {cardinal} ({elev_desc}, {elevation}°) • {alt_ft:,} ft")

    msg = "\n".join(lines)
    # If too long for Bounce screen (> 138 chars), compact route
    if len(msg) > 138 and route and " (" in route:
        orig_city = (flight_data.get("route_info") or {}).get("origin_city")
        dest_city = (flight_data.get("route_info") or {}).get("destination_city")
        if orig_city and dest_city:
            compact_route = f"{orig_city} ➔ {dest_city}"
            lines[1] = f"🛫 {compact_route}"
            msg = "\n".join(lines)

    return msg
