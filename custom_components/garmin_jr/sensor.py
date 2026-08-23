"""Sensor platform for Garmin Jr."""
from __future__ import annotations

import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_ACTIVE_MINUTES,
    ATTR_ACTIVE_MINUTES_RECORD,
    ATTR_BATTERY_LEVEL,
    ATTR_BATTERY_STATUS,
    ATTR_CHILD_ID,
    ATTR_CHILD_NAME,
    ATTR_DAILY_STEP_GOAL,
    ATTR_DEVICE_ID,
    ATTR_FAMILY_ID,
    ATTR_FAMILY_NAME,
    ATTR_LAST_SYNC,
    ATTR_MODEL,
    ATTR_STEPS,
    ATTR_STEPS_RECORD,
    DOMAIN,
)
from .coordinator import GarminJrDataUpdateCoordinator

SENSOR_TYPES: dict[str, dict[str, Any]] = {
    "steps": {
        "name": "Daily Steps",
        "icon": "mdi:walk",
        "device_class": None,
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "unit": "steps",
        "data_key": ATTR_STEPS,
    },
    "step_goal": {
        "name": "Daily Step Goal",
        "icon": "mdi:flag-checkered",
        "device_class": None,
        "state_class": None,
        "unit": "steps",
        "data_key": ATTR_DAILY_STEP_GOAL,
    },
    "steps_record": {
        "name": "Steps Record",
        "icon": "mdi:trophy-outline",
        "device_class": None,
        "state_class": None,
        "unit": "steps",
        "data_key": ATTR_STEPS_RECORD,
    },
    "active_minutes_record": {
        "name": "Active Minutes Record",
        "icon": "mdi:trophy-award",
        "device_class": SensorDeviceClass.DURATION,
        "state_class": None,
        "unit": UnitOfTime.MINUTES,
        "data_key": ATTR_ACTIVE_MINUTES_RECORD,
    },
    "battery": {
        "name": "Battery",
        "icon": "mdi:battery",
        "device_class": SensorDeviceClass.BATTERY,
        "state_class": SensorStateClass.MEASUREMENT,
        "unit": PERCENTAGE,
        "data_key": ATTR_BATTERY_LEVEL,
    },
    "active_minutes": {
        "name": "Active Minutes",
        "icon": "mdi:timer-outline",
        "device_class": SensorDeviceClass.DURATION,
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "unit": UnitOfTime.MINUTES,
        "data_key": ATTR_ACTIVE_MINUTES,
    },
    "last_sync": {
        "name": "Last Sync",
        "icon": "mdi:sync",
        "device_class": SensorDeviceClass.TIMESTAMP,
        "state_class": None,
        "unit": None,
        "data_key": ATTR_LAST_SYNC,
    },
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Garmin Jr sensors from config entry."""
    coordinator: GarminJrDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    tracked_sensors: set[str] = set()

    @callback
    def _check_entities() -> None:
        if not coordinator.data:
            return

        new_entities: list[GarminJrSensorEntity] = []
        for child_id, _ in coordinator.data.items():
            for s_type in SENSOR_TYPES:
                key = f"{child_id}_{s_type}"
                if key not in tracked_sensors:
                    tracked_sensors.add(key)
                    new_entities.append(
                        GarminJrSensorEntity(coordinator, child_id, s_type)
                    )

        if new_entities:
            async_add_entities(new_entities)

    _check_entities()
    entry.async_on_unload(coordinator.async_add_listener(_check_entities))


class GarminJrSensorEntity(
    CoordinatorEntity[GarminJrDataUpdateCoordinator], SensorEntity
):
    """Representation of a Garmin Jr sensor entity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GarminJrDataUpdateCoordinator,
        child_id: str,
        sensor_type: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.child_id = child_id
        self.sensor_type = sensor_type
        self.spec = SENSOR_TYPES[sensor_type]
        self._attr_unique_id = f"garmin_jr_{child_id}_{sensor_type}"
        self._attr_device_class = self.spec["device_class"]
        self._attr_state_class = self.spec["state_class"]
        self._attr_native_unit_of_measurement = self.spec["unit"]
        self._attr_icon = self.spec["icon"]

    @property
    def _child_data(self) -> dict[str, Any]:
        """Return the current child data from the coordinator."""
        return self.coordinator.data.get(self.child_id, {}) if self.coordinator.data else {}

    @property
    def name(self) -> str:
        """Return entity display name."""
        return self.spec["name"]

    @property
    def native_value(self) -> Any:
        """Return state of the sensor."""
        data = self._child_data
        val = data.get(self.spec["data_key"])
        if self.sensor_type == "last_sync" and val is not None:
            if isinstance(val, str):
                try:
                    dt = datetime.datetime.fromisoformat(val.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=datetime.timezone.utc)
                    return dt
                except Exception:
                    pass
            elif isinstance(val, (int, float)):
                try:
                    ts = val / 1000.0 if val > 1e11 else float(val)
                    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
                except Exception:
                    pass
        return val

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        data = self._child_data
        attrs: dict[str, Any] = {
            "child_id": self.child_id,
            "device_id": data.get(ATTR_DEVICE_ID),
            "last_sync": data.get(ATTR_LAST_SYNC),
            "family_id": data.get(ATTR_FAMILY_ID),
            "family_name": data.get(ATTR_FAMILY_NAME),
        }
        if self.sensor_type == "steps":
            attrs["daily_step_goal"] = data.get(ATTR_DAILY_STEP_GOAL)
            attrs["steps_record"] = data.get(ATTR_STEPS_RECORD)
        elif self.sensor_type == "battery":
            attrs["battery_status"] = data.get(ATTR_BATTERY_STATUS)
        return attrs

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for Home Assistant registry."""
        data = self._child_data
        child_name = data.get(ATTR_CHILD_NAME, "Garmin Jr Child")
        return DeviceInfo(
            identifiers={(DOMAIN, self.child_id)},
            name=child_name,
            manufacturer="Garmin",
            model=data.get(ATTR_MODEL, "Garmin Bounce 2"),
            serial_number=str(data.get(ATTR_DEVICE_ID, "")),
        )
