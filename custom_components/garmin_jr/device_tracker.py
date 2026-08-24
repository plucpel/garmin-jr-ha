"""Device tracker platform for Garmin Jr."""
from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_ACCURACY,
    ATTR_BATTERY_STATUS,
    ATTR_CHILD_ID,
    ATTR_CHILD_NAME,
    ATTR_DEVICE_ID,
    ATTR_FIX_TYPE,
    ATTR_GARMIN_GEOFENCE_ID,
    ATTR_GARMIN_SAFE_ZONE,
    ATTR_HAS_WIFI,
    ATTR_LAST_SYNC,
    ATTR_LATITUDE,
    ATTR_LOCATION_TIMESTAMP,
    ATTR_LONGITUDE,
    ATTR_MATCHED_HA_ZONE,
    ATTR_MODEL,
    CONF_ZONE_MAPPING,
    DOMAIN,
)
from .coordinator import GarminJrDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Garmin Jr device tracker from config entry."""
    coordinator: GarminJrDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    tracked_children: set[str] = set()

    @callback
    def _check_entities() -> None:
        if not coordinator.data:
            return

        new_entities: list[GarminJrTrackerEntity] = []
        for child_id, child_data in coordinator.data.items():
            if child_id not in tracked_children:
                tracked_children.add(child_id)
                new_entities.append(GarminJrTrackerEntity(coordinator, entry, child_id))

        if new_entities:
            async_add_entities(new_entities)

    _check_entities()
    entry.async_on_unload(coordinator.async_add_listener(_check_entities))


class GarminJrTrackerEntity(
    CoordinatorEntity[GarminJrDataUpdateCoordinator], TrackerEntity
):
    """Representation of a Garmin Jr device tracker with zone coupling."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:watch-gps"

    def __init__(
        self,
        coordinator: GarminJrDataUpdateCoordinator,
        entry: ConfigEntry,
        child_id: str,
    ) -> None:
        """Initialize the device tracker."""
        super().__init__(coordinator)
        self.entry = entry
        self.child_id = child_id
        self._attr_unique_id = f"garmin_jr_{child_id}_tracker"

    @property
    def _child_data(self) -> dict[str, Any]:
        """Return the current child data from the coordinator."""
        return self.coordinator.data.get(self.child_id, {}) if self.coordinator.data else {}

    @property
    def name(self) -> str:
        """Return entity display name."""
        return "Location"

    @property
    def source_type(self) -> SourceType:
        """Return the source type, e.g. GPS."""
        return SourceType.GPS

    def _resolve_zone_coordinates(self) -> tuple[float | None, float | None, str | None]:
        """Resolve coordinates considering Garmin geofences and HA zone mappings."""
        data = self._child_data
        raw_lat = data.get(ATTR_LATITUDE)
        raw_lon = data.get(ATTR_LONGITUDE)
        geofence_id = str(data.get(ATTR_GARMIN_GEOFENCE_ID) or "")
        safe_zone_name = data.get(ATTR_GARMIN_SAFE_ZONE)

        # If not actively in a Garmin safe zone, return raw coordinates
        if not geofence_id or not safe_zone_name:
            lat = float(raw_lat) if raw_lat is not None else None
            lon = float(raw_lon) if raw_lon is not None else None
            return lat, lon, None

        # Check configured zone mapping
        zone_mapping = self.entry.options.get(CONF_ZONE_MAPPING, {})
        target_mode = zone_mapping.get(geofence_id, "auto")

        # 1. User explicitly selected "None / Raw GPS"
        if target_mode == "none":
            lat = float(raw_lat) if raw_lat is not None else None
            lon = float(raw_lon) if raw_lon is not None else None
            return lat, lon, None

        # 2. User mapped to a specific HA zone (e.g. "zone.home" or "zone.school")
        if target_mode.startswith("zone.") and self.hass:
            zone_state = self.hass.states.get(target_mode)
            if zone_state and "latitude" in zone_state.attributes and "longitude" in zone_state.attributes:
                return (
                    float(zone_state.attributes["latitude"]),
                    float(zone_state.attributes["longitude"]),
                    target_mode,
                )

        # 3. Auto-detect: search for an existing HA zone with a matching name
        if target_mode == "auto" and self.hass:
            zone_entities = self.hass.states.async_entity_ids("zone")
            for z_id in zone_entities:
                z_state = self.hass.states.get(z_id)
                if not z_state:
                    continue
                z_name = z_state.attributes.get("friendly_name") or z_id.split(".", 1)[-1]
                if z_name.strip().lower() == safe_zone_name.strip().lower():
                    return (
                        float(z_state.attributes["latitude"]),
                        float(z_state.attributes["longitude"]),
                        z_id,
                    )

        # 4. Fallback to Garmin's geofence center coordinates if available
        gf_lat = data.get("geofence_latitude")
        gf_lon = data.get("geofence_longitude")
        if gf_lat is not None and gf_lon is not None:
            return float(gf_lat), float(gf_lon), f"garmin_{safe_zone_name}"

        # 5. Fallback to raw coordinates
        lat = float(raw_lat) if raw_lat is not None else None
        lon = float(raw_lon) if raw_lon is not None else None
        return lat, lon, None

    @property
    def latitude(self) -> float | None:
        """Return latitude value of the device."""
        lat, _, _ = self._resolve_zone_coordinates()
        return lat

    @property
    def longitude(self) -> float | None:
        """Return longitude value of the device."""
        _, lon, _ = self._resolve_zone_coordinates()
        return lon

    @property
    def location_accuracy(self) -> int:
        """Return the location accuracy of the device in meters."""
        return int(self._child_data.get(ATTR_ACCURACY, 15))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        data = self._child_data
        _, _, matched_zone = self._resolve_zone_coordinates()
        return {
            "child_id": self.child_id,
            "device_id": data.get(ATTR_DEVICE_ID),
            "battery_status": data.get(ATTR_BATTERY_STATUS),
            "last_sync": data.get(ATTR_LAST_SYNC),
            "location_timestamp": data.get(ATTR_LOCATION_TIMESTAMP),
            "garmin_safe_zone": data.get(ATTR_GARMIN_SAFE_ZONE),
            "garmin_geofence_id": data.get(ATTR_GARMIN_GEOFENCE_ID),
            "matched_ha_zone": matched_zone,
            "fix_type": data.get(ATTR_FIX_TYPE),
            "has_wifi": data.get(ATTR_HAS_WIFI, False),
        }

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for Home Assistant registry."""
        data = self._child_data
        child_name = data.get(ATTR_CHILD_NAME, "Garmin Jr Child")
        return DeviceInfo(
            identifiers={(DOMAIN, self.child_id)},
            name=child_name,
            manufacturer="Garmin",
            model=data.get(ATTR_MODEL, "Garmin Bounce / Jr"),
            serial_number=str(data.get(ATTR_DEVICE_ID, "")),
        )

