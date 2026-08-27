"""Garmin Jr Integration for Home Assistant."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceResponse, SupportsResponse

from .const import (
    ATTR_CHILD_ID,
    ATTR_CHILD_NAME,
    ATTR_LANGUAGE,
    ATTR_MESSAGE,
    ATTR_SEND_TO_WATCH,
    ATTR_TARGET,
    CONF_DI_CLIENT_ID,
    CONF_DI_REFRESH_TOKEN,
    CONF_DI_TOKEN,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_TOKEN_DATA,
    DOMAIN,
    LOGGER,
    PLATFORMS,
    SERVICE_REQUEST_LOCATION_UPDATE,
    SERVICE_SEND_MESSAGE,
    SERVICE_SPOT_PLANE,
)
from .coordinator import GarminJrDataUpdateCoordinator
from .garmin_client import GarminJrClient
from .plane_spotter import (
    enrich_flight_details,
    fetch_live_aircraft_sync,
    filter_and_rank_planes,
    format_bounce_response,
    resolve_kid_location,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the Garmin Jr component services."""
    hass.data.setdefault(DOMAIN, {})

    async def async_handle_send_message(call: Any) -> None:
        """Handle send_message service call."""
        target = str(call.data.get(ATTR_TARGET, "")).strip()
        message = str(call.data.get(ATTR_MESSAGE, "")).strip()

        if not message:
            _LOGGER.warning("Garmin Jr send_message called with empty message")
            return

        for entry_id, coordinator in hass.data.get(DOMAIN, {}).items():
            if not isinstance(coordinator, GarminJrDataUpdateCoordinator) or not coordinator.data:
                continue

            target_pk = None
            # Match by ID or Name dynamically
            for kid_id, kid_data in coordinator.data.items():
                if not target or target.lower() in (kid_id.lower(), kid_data.get(ATTR_CHILD_NAME, "").lower()):
                    target_pk = kid_data.get("connectId") or kid_data.get("userProfilePk") or (137662175 if kid_id == "15839246" else kid_id)
                    break

            if target_pk:
                success = await hass.async_add_executor_job(
                    coordinator.client.send_text_message, target_pk, message
                )
                if success:
                    _LOGGER.debug("Sent Garmin Jr message to %s", target_pk)
                    await coordinator.async_request_refresh()
                return

        _LOGGER.warning("Could not find Garmin Jr child profile matching target: %s", target)

    async def async_handle_request_location_update(call: Any) -> None:
        """Handle request_location_update service call."""
        target = str(call.data.get(ATTR_TARGET, "")).strip()

        for entry_id, coordinator in hass.data.get(DOMAIN, {}).items():
            if not isinstance(coordinator, GarminJrDataUpdateCoordinator) or not coordinator.data:
                continue

            target_kid_id = None
            for kid_id, kid_data in coordinator.data.items():
                if not target or target.lower() in (kid_id.lower(), kid_data.get(ATTR_CHILD_NAME, "").lower()):
                    target_kid_id = kid_id
                    break

            if target_kid_id:
                success = await hass.async_add_executor_job(
                    coordinator.client.request_location_update, target_kid_id
                )
                if success:
                    _LOGGER.debug("Requested location refresh for %s", target_kid_id)
                    await coordinator.async_request_refresh()
                return

        _LOGGER.warning("Could not find Garmin Jr child profile matching target: %s", target)

    async def async_handle_spot_plane(call: Any) -> ServiceResponse:
        """Handle spot_plane service call: find overhead planes and optionally message watch."""
        target = str(call.data.get(ATTR_TARGET, "")).strip()
        send_to_watch = call.data.get(ATTR_SEND_TO_WATCH, True)
        language = str(call.data.get(ATTR_LANGUAGE, "fr")).strip().lower()

        for entry_id, coordinator in hass.data.get(DOMAIN, {}).items():
            if not isinstance(coordinator, GarminJrDataUpdateCoordinator) or not coordinator.data:
                continue

            target_kid_id = None
            target_kid_data = None
            target_pk = None

            for kid_id, kid_data in coordinator.data.items():
                if not target or target.lower() in (kid_id.lower(), kid_data.get(ATTR_CHILD_NAME, "").lower()):
                    target_kid_id = kid_id
                    target_kid_data = kid_data
                    target_pk = kid_data.get("connectId") or kid_data.get("userProfilePk") or (137662175 if kid_id == "15839246" else kid_id)
                    break

            if target_kid_id and target_kid_data:
                # 1. Resolve 3-tier child location
                loc_info = resolve_kid_location(hass, target_kid_data, target_kid_id)

                # Asynchronous background refresh if location is stale
                if loc_info.get("stale") and loc_info.get("source") == "watch_gps":
                    _LOGGER.debug("Dispatching async background location refresh for %s (stale location)", target_kid_id)
                    hass.async_create_task(
                        hass.async_add_executor_job(
                            coordinator.client.request_location_update, target_kid_id
                        )
                    )

                # 2. Fetch live aircraft around coordinates
                aircraft_raw = await hass.async_add_executor_job(
                    fetch_live_aircraft_sync, loc_info["latitude"], loc_info["longitude"], 35.0
                )

                # 3. Filter and rank planes by sightline & elevation
                ranked_planes = filter_and_rank_planes(
                    loc_info["latitude"], loc_info["longitude"], aircraft_raw, max_distance_km=30.0, min_elevation_deg=12.0
                )

                top_plane = None
                if ranked_planes:
                    top_plane = enrich_flight_details(ranked_planes[0], language=language)

                # 4. Format kid-friendly watch message
                formatted_msg = format_bounce_response(top_plane, loc_info, language=language)

                # 5. Optionally send to watch
                if send_to_watch and target_pk:
                    await hass.async_add_executor_job(
                        coordinator.client.send_text_message, target_pk, formatted_msg
                    )

                return {
                    "found": bool(top_plane),
                    "message": formatted_msg,
                    "flight": top_plane or {},
                    "location": loc_info,
                    "total_nearby_aircraft": len(ranked_planes),
                }

        _LOGGER.warning("Could not find Garmin Jr child profile matching target: %s", target)
        return {
            "found": False,
            "message": "Enfant non trouvé",
            "flight": {},
            "location": {},
            "total_nearby_aircraft": 0,
        }

    hass.services.async_register(DOMAIN, SERVICE_SEND_MESSAGE, async_handle_send_message)
    hass.services.async_register(
        DOMAIN, SERVICE_REQUEST_LOCATION_UPDATE, async_handle_request_location_update
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SPOT_PLANE,
        async_handle_spot_plane,
        supports_response=SupportsResponse.OPTIONAL,
    )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Garmin Jr from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    token_data = entry.data.get(CONF_TOKEN_DATA)
    if not token_data and entry.data.get(CONF_DI_TOKEN):
        token_data = {
            "di_token": entry.data.get(CONF_DI_TOKEN),
            "di_refresh_token": entry.data.get(CONF_DI_REFRESH_TOKEN),
            "di_client_id": entry.data.get(CONF_DI_CLIENT_ID),
        }

    email = entry.data.get(CONF_EMAIL)
    password = entry.data.get(CONF_PASSWORD)

    client = GarminJrClient(
        email=email,
        password=password,
        token_data=token_data,
    )

    coordinator = GarminJrDataUpdateCoordinator(hass, entry, client)

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    coordinator.async_start_message_polling()
    entry.async_on_unload(coordinator.async_unload)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator and hasattr(coordinator, "async_unload"):
        coordinator.async_unload()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)

