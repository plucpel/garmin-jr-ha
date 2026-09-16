"""Garmin Jr Integration for Home Assistant."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceResponse, SupportsResponse
from homeassistant.helpers import device_registry as dr

from .ai_bridge import GarminBounceAiBridge
from .const import (
    ATTR_CHILD_ID,
    ATTR_CHILD_NAME,
    ATTR_LANGUAGE,
    ATTR_MESSAGE,
    ATTR_SEND_TO_WATCH,
    ATTR_TARGET,
    ATTR_TEXT,
    CONF_CHILD_PROFILE_PKS,
    CONF_DI_CLIENT_ID,
    CONF_DI_REFRESH_TOKEN,
    CONF_DI_TOKEN,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_TOKEN_DATA,
    DOMAIN,
    LOGGER,
    PLATFORMS,
    SERVICE_PROCESS_BOUNCE_MESSAGE,
    SERVICE_REQUEST_LOCATION_UPDATE,
    SERVICE_SEND_MESSAGE,
    SERVICE_SPOT_PLANE,
)
from .coordinator import (
    FAST_MESSAGE_POLL_INTERVAL_SECONDS,
    GarminJrDataUpdateCoordinator,
    resolve_child_profile_pk,
)
from .garmin_client import GarminJrClient
from .plane_spotter import (
    enrich_flight_details,
    fetch_live_aircraft_sync,
    filter_and_rank_planes,
    format_bounce_response,
    resolve_kid_location,
)

_LOGGER = logging.getLogger(__name__)


def find_child_target(
    hass: HomeAssistant, target: str
) -> tuple[GarminJrDataUpdateCoordinator | None, str | None, dict[str, Any] | None, str | int | None, str | int | None]:
    """Find a coordinator and child matching the target string by ID or Name.

    Returns: (coordinator, kid_id, kid_data, target_pk, target_device_id)
    """
    for entry_id, coordinator in hass.data.get(DOMAIN, {}).items():
        if not isinstance(coordinator, GarminJrDataUpdateCoordinator) or not coordinator.data:
            continue

        for kid_id, kid_data in coordinator.data.items():
            if not target or target.lower() in (
                str(kid_id).lower(),
                str(kid_data.get(ATTR_CHILD_NAME, "")).lower(),
            ):
                coord_entry = getattr(coordinator, "entry", None)
                options = getattr(coord_entry, "options", None) if coord_entry else None
                coord_client = getattr(coordinator, "client", None)
                learned_pks = getattr(coord_client, "_learned_child_pks", None) if coord_client else None

                target_pk = resolve_child_profile_pk(
                    kid_id,
                    kid_data,
                    options=options,
                    learned_pks=learned_pks,
                )
                target_device_id = kid_data.get("device_id") or kid_data.get("deviceId") or kid_id
                return coordinator, kid_id, kid_data, target_pk, target_device_id

    return None, None, None, None, None


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the Garmin Jr component services."""
    hass.data.setdefault(DOMAIN, {})
    ai_bridge = GarminBounceAiBridge(hass)
    hass.data[DOMAIN]["ai_bridge"] = ai_bridge

    async def async_handle_send_message(call: Any) -> None:
        """Handle send_message service call."""
        target = str(call.data.get(ATTR_TARGET, "")).strip()
        message = str(call.data.get(ATTR_MESSAGE, "")).strip()

        if not message:
            _LOGGER.warning("Garmin Jr send_message called with empty message")
            return

        coordinator, kid_id, kid_data, target_pk, _ = find_child_target(hass, target)
        if coordinator and target_pk:
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

        coordinator, kid_id, kid_data, _, target_device_id = find_child_target(hass, target)
        if coordinator and target_device_id:
            success = await hass.async_add_executor_job(
                coordinator.client.request_location_update, target_device_id
            )
            if success:
                _LOGGER.debug("Requested location refresh for %s", target_device_id)
                await coordinator.async_request_refresh()
            return

        _LOGGER.warning("Could not find Garmin Jr child profile matching target: %s", target)

    async def async_handle_spot_plane(call: Any) -> ServiceResponse:
        """Handle spot_plane service call: find overhead planes and optionally message watch."""
        target = str(call.data.get(ATTR_TARGET, "")).strip()
        send_to_watch = call.data.get(ATTR_SEND_TO_WATCH, True)
        language = str(call.data.get(ATTR_LANGUAGE, "fr")).strip().lower()

        coordinator, target_kid_id, target_kid_data, target_pk, target_device_id = find_child_target(hass, target)
        if coordinator and target_kid_id and target_kid_data:
            # 1. Resolve 3-tier child location
            loc_info = resolve_kid_location(hass, target_kid_data, target_kid_id)

            # Asynchronous background refresh if location is stale
            if loc_info.get("stale") and loc_info.get("source") == "watch_gps":
                _LOGGER.debug("Dispatching async background location refresh for %s (stale location)", target_kid_id)
                device_target = target_device_id
                async def _bg_location_refresh() -> None:
                    try:
                        await hass.async_add_executor_job(
                            coordinator.client.request_location_update, device_target
                        )
                    except Exception as ex:
                        _LOGGER.debug("Background location refresh failed for %s: %s", device_target, ex)

                hass.async_create_background_task(_bg_location_refresh(), name="garmin_jr_bg_location_refresh")

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
                if ai_bridge:
                    ai_bridge.get_session(target_kid_id).set_spotted_flight(top_plane)

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

    async def async_handle_process_bounce_message(call: Any) -> ServiceResponse:
        """Handle incoming message from Bounce watch through Strix Halo NPU AI bridge."""
        target = str(call.data.get(ATTR_TARGET, "")).strip()
        incoming_text = str(call.data.get(ATTR_TEXT) or call.data.get(ATTR_MESSAGE) or "").strip()
        send_to_watch = call.data.get(ATTR_SEND_TO_WATCH, True)

        coordinator, target_kid_id, target_kid_data, target_pk, _ = find_child_target(hass, target)
        if coordinator and target_kid_id and target_kid_data:
            child_name = target_kid_data.get(ATTR_CHILD_NAME, "Child")
            reply = await hass.async_add_executor_job(
                ai_bridge.process_incoming_message,
                target_kid_id,
                child_name,
                incoming_text,
                target_kid_data,
            )

            if send_to_watch and target_pk and reply:
                await hass.async_add_executor_job(
                    coordinator.client.send_text_message, target_pk, reply
                )

            return {
                "reply": reply,
                "child_id": target_kid_id,
                "child_name": child_name,
            }

        _LOGGER.warning("Could not find Garmin Jr child profile matching target: %s", target)
        return {
            "reply": "Enfant non trouvé",
            "child_id": None,
            "child_name": None,
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
    hass.services.async_register(
        DOMAIN,
        SERVICE_PROCESS_BOUNCE_MESSAGE,
        async_handle_process_bounce_message,
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
    """Reload config entry when user options change, ignoring internal learned PK persistence."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if isinstance(coordinator, GarminJrDataUpdateCoordinator):
        new_user_opts = {k: v for k, v in entry.options.items() if k != CONF_CHILD_PROFILE_PKS}
        last_user_opts = getattr(coordinator, "_last_user_options", None)
        if last_user_opts is not None and new_user_opts == last_user_opts:
            _LOGGER.debug("Garmin Jr: Skipping entry reload for internal learned profile PKs persistence")
            return

    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Remove a config entry from a device if no longer in use."""
    coordinator: GarminJrDataUpdateCoordinator | None = hass.data.get(DOMAIN, {}).get(config_entry.entry_id)
    if not coordinator or not coordinator.data:
        return True

    active_child_ids = {str(cid) for cid in coordinator.data.keys()}
    for domain, ident in device_entry.identifiers:
        if domain == DOMAIN and str(ident) in active_child_ids:
            return False
    return True


