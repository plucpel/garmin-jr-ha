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
    ATTR_LAST_SYNC,
    ATTR_LATITUDE,
    ATTR_LOCATION_TIMESTAMP,
    ATTR_LONGITUDE,
    ATTR_MODEL,
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
                new_entities.append(GarminJrTrackerEntity(coordinator, child_id))

        if new_entities:
            async_add_entities(new_entities)

    _check_entities()
    entry.async_on_unload(coordinator.async_add_listener(_check_entities))


class GarminJrTrackerEntity(
    CoordinatorEntity[GarminJrDataUpdateCoordinator], TrackerEntity
):
    """Representation of a Garmin Jr device tracker."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:watch-gps"

    def __init__(
        self,
        coordinator: GarminJrDataUpdateCoordinator,
        child_id: str,
    ) -> None:
        """Initialize the device tracker."""
        super().__init__(coordinator)
        self.child_id = child_id
        self._attr_unique_id = f"garmin_jr_{child_id}_tracker"

    @property
    def _child_data(self) -> dict[str, Any]:
        """Return the current child data from the coordinator."""
        return self.coordinator.data.get(self.child_id, {}) if self.coordinator.data else {}

    @property
    def name(self) -> str:
        """Return entity display name."""
        child_name = self._child_data.get(ATTR_CHILD_NAME, "Child")
        return f"{child_name} Location"

    @property
    def source_type(self) -> SourceType:
        """Return the source type, e.g. GPS."""
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        """Return latitude value of the device."""
        lat = self._child_data.get(ATTR_LATITUDE)
        return float(lat) if lat is not None else None

    @property
    def longitude(self) -> float | None:
        """Return longitude value of the device."""
        lon = self._child_data.get(ATTR_LONGITUDE)
        return float(lon) if lon is not None else None

    @property
    def location_accuracy(self) -> int:
        """Return the location accuracy of the device in meters."""
        return int(self._child_data.get(ATTR_ACCURACY, 15))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        data = self._child_data
        return {
            "child_id": self.child_id,
            "device_id": data.get(ATTR_DEVICE_ID),
            "battery_status": data.get(ATTR_BATTERY_STATUS),
            "last_sync": data.get(ATTR_LAST_SYNC),
            "location_timestamp": data.get(ATTR_LOCATION_TIMESTAMP),
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
