"""Sensor platform for Garmin Jr."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_ACTIVE_MINUTES,
    ATTR_BATTERY_LEVEL,
    ATTR_BATTERY_STATUS,
    ATTR_CHILD_NAME,
    ATTR_DAILY_STEP_GOAL,
    ATTR_DEVICE_ID,
    ATTR_LAST_SYNC,
    ATTR_MODEL,
    ATTR_STEPS,
    DOMAIN,
)
from .coordinator import GarminJrDataUpdateCoordinator


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

        new_entities: list[SensorEntity] = []
        for child_id, child_data in coordinator.data.items():
            # Steps sensor
            key_steps = f"{child_id}_steps"
            if key_steps not in tracked_sensors:
                tracked_sensors.add(key_steps)
                new_entities.append(GarminJrStepsSensor(coordinator, child_id))

            # Battery sensor
            key_battery = f"{child_id}_battery"
            if key_battery not in tracked_sensors:
                tracked_sensors.add(key_battery)
                new_entities.append(GarminJrBatterySensor(coordinator, child_id))

            # Active minutes sensor
            key_active = f"{child_id}_active_minutes"
            if key_active not in tracked_sensors:
                tracked_sensors.add(key_active)
                new_entities.append(GarminJrActiveMinutesSensor(coordinator, child_id))

            # Last sync sensor
            key_sync = f"{child_id}_last_sync"
            if key_sync not in tracked_sensors:
                tracked_sensors.add(key_sync)
                new_entities.append(GarminJrLastSyncSensor(coordinator, child_id))

        if new_entities:
            async_add_entities(new_entities)

    _check_entities()
    entry.async_on_unload(coordinator.async_add_listener(_check_entities))


class GarminJrBaseSensor(CoordinatorEntity[GarminJrDataUpdateCoordinator], SensorEntity):
    """Base sensor for Garmin Jr child data."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GarminJrDataUpdateCoordinator,
        child_id: str,
        sensor_type: str,
    ) -> None:
        """Initialize base sensor."""
        super().__init__(coordinator)
        self.child_id = child_id
        self.sensor_type = sensor_type
        self._attr_unique_id = f"garmin_jr_{child_id}_{sensor_type}"

    @property
    def _child_data(self) -> dict[str, Any]:
        """Return current child data dictionary."""
        return self.coordinator.data.get(self.child_id, {}) if self.coordinator.data else {}

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        data = self._child_data
        child_name = data.get(ATTR_CHILD_NAME, "Garmin Jr Child")
        return DeviceInfo(
            identifiers={(DOMAIN, self.child_id)},
            name=child_name,
            manufacturer="Garmin",
            model=data.get(ATTR_MODEL, "Garmin Bounce / Jr"),
            serial_number=str(data.get(ATTR_DEVICE_ID, "")),
        )


class GarminJrStepsSensor(GarminJrBaseSensor):
    """Sensor for daily step count."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "steps"
    _attr_icon = "mdi:walk"

    def __init__(self, coordinator: GarminJrDataUpdateCoordinator, child_id: str) -> None:
        super().__init__(coordinator, child_id, "steps")

    @property
    def name(self) -> str:
        return "Daily Steps"

    @property
    def native_value(self) -> int:
        return int(self._child_data.get(ATTR_STEPS, 0))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "daily_goal": self._child_data.get(ATTR_DAILY_STEP_GOAL, 6000),
        }


class GarminJrBatterySensor(GarminJrBaseSensor):
    """Sensor for device battery level."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: GarminJrDataUpdateCoordinator, child_id: str) -> None:
        super().__init__(coordinator, child_id, "battery")

    @property
    def name(self) -> str:
        return "Battery"

    @property
    def native_value(self) -> int | None:
        val = self._child_data.get(ATTR_BATTERY_LEVEL)
        return int(val) if val is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "status": self._child_data.get(ATTR_BATTERY_STATUS, "NORMAL"),
        }


class GarminJrActiveMinutesSensor(GarminJrBaseSensor):
    """Sensor for daily active minutes."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_icon = "mdi:clock-fast"

    def __init__(self, coordinator: GarminJrDataUpdateCoordinator, child_id: str) -> None:
        super().__init__(coordinator, child_id, "active_minutes")

    @property
    def name(self) -> str:
        return "Active Minutes"

    @property
    def native_value(self) -> int:
        return int(self._child_data.get(ATTR_ACTIVE_MINUTES, 0))


class GarminJrLastSyncSensor(GarminJrBaseSensor):
    """Sensor for last synchronization timestamp."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:sync"

    def __init__(self, coordinator: GarminJrDataUpdateCoordinator, child_id: str) -> None:
        super().__init__(coordinator, child_id, "last_sync")

    @property
    def name(self) -> str:
        return "Last Sync"

    @property
    def native_value(self) -> datetime | None:
        ts_str = self._child_data.get(ATTR_LAST_SYNC)
        if not ts_str:
            return None
        try:
            return dt_util.parse_datetime(ts_str)
        except Exception:
            return None
