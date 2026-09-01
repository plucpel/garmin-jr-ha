"""Switch platform for Garmin Jr."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_CHILD_ID,
    ATTR_CHILD_NAME,
    ATTR_DEVICE_ID,
    ATTR_MODEL,
    DOMAIN,
)
from .coordinator import (
    GarminJrDataUpdateCoordinator,
    get_child_operating_mode,
    get_child_school_mode_end_time,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Garmin Jr switches from config entry."""
    coordinator: GarminJrDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    tracked_switches: set[str] = set()

    @callback
    def _check_entities() -> None:
        if not coordinator.data:
            return

        new_entities: list[GarminJrSchoolModeSwitch] = []
        for child_id, _ in coordinator.data.items():
            key = f"{child_id}_school_mode"
            if key not in tracked_switches:
                tracked_switches.add(key)
                new_entities.append(
                    GarminJrSchoolModeSwitch(coordinator, child_id)
                )

        if new_entities:
            async_add_entities(new_entities)

    _check_entities()
    entry.async_on_unload(coordinator.async_add_listener(_check_entities))


class GarminJrSchoolModeSwitch(
    CoordinatorEntity[GarminJrDataUpdateCoordinator], SwitchEntity
):
    """Representation of a Garmin Jr School Mode switch."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:school"

    def __init__(
        self,
        coordinator: GarminJrDataUpdateCoordinator,
        child_id: str,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self.child_id = child_id
        self._attr_unique_id = f"garmin_jr_{child_id}_school_mode_switch"
        self._attr_name = "School Mode"
        self._manual_override: bool | None = None

    @property
    def _child_data(self) -> dict[str, Any]:
        """Return the current child data from the coordinator."""
        if self.coordinator.data and self.child_id in self.coordinator.data:
            return self.coordinator.data[self.child_id]
        return {}

    @property
    def is_on(self) -> bool:
        """Return true if School Mode is enabled for this child."""
        if self._manual_override is not None:
            return self._manual_override

        child_data = self._child_data
        settings = child_data.get("settings") or {}
        school_mode = child_data.get("school_mode") or settings.get("schoolMode") or settings.get("school_mode") or {}

        mode_val = school_mode.get("mode") or school_mode.get("enabled")
        if isinstance(mode_val, str):
            return mode_val.upper() in ("RESTRICTED", "SILENT", "ALL", "ON", "TRUE")
        if isinstance(mode_val, bool):
            return mode_val
        return True  # Default on if schedule is configured

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on School Mode."""
        self._manual_override = True
        child_data = self._child_data
        if "school_mode" in child_data and isinstance(child_data["school_mode"], dict):
            child_data["school_mode"]["mode"] = "Restricted"
            child_data["school_mode"]["enabled"] = True
        self.coordinator.set_child_school_mode_override(self.child_id, True)
        self.async_write_ha_state()
        _LOGGER.info("Garmin Jr: School Mode enabled for child %s", self.child_id)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off School Mode (e.g. for holiday / vacation / weekend)."""
        self._manual_override = False
        child_data = self._child_data
        if "school_mode" in child_data and isinstance(child_data["school_mode"], dict):
            child_data["school_mode"]["mode"] = "Off"
            child_data["school_mode"]["enabled"] = False
        self.coordinator.set_child_school_mode_override(self.child_id, False)
        # Immediately lift any coordinator pause
        self.coordinator.reset_school_mode_pause()
        self.async_write_ha_state()
        _LOGGER.info(
            "Garmin Jr: School Mode disabled for child %s (holiday/override). Full tracking active.",
            self.child_id,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes for School Mode schedule."""
        child_data = self._child_data
        settings = child_data.get("settings") or {}
        school_mode = child_data.get("school_mode") or settings.get("schoolMode") or settings.get("school_mode") or {}

        start_time = school_mode.get("startTime") or school_mode.get("start_time") or "08:00"
        end_time = school_mode.get("endTime") or school_mode.get("end_time") or "15:00"
        days = school_mode.get("days") or ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        mode = "Off" if not self.is_on else (school_mode.get("mode") or "Restricted")

        in_school = get_child_school_mode_end_time(child_data) is not None if self.is_on else False
        operating_mode = get_child_operating_mode(child_data) if self.is_on else "active"

        return {
            "start_time": start_time,
            "end_time": end_time,
            "days": days,
            "mode": mode,
            "in_school_mode": in_school,
            "operating_mode": operating_mode,
            "holiday_override": self._manual_override is False,
        }

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this child's watch."""
        child_data = self._child_data
        child_name = child_data.get(ATTR_CHILD_NAME) or f"Child {self.child_id}"
        device_id = child_data.get(ATTR_DEVICE_ID) or self.child_id
        model = child_data.get(ATTR_MODEL) or "Garmin Bounce"

        return DeviceInfo(
            identifiers={(DOMAIN, str(device_id))},
            name=f"{child_name}'s {model}",
            manufacturer="Garmin",
            model=model,
            via_device=(DOMAIN, str(self.coordinator.config_entry.entry_id)),
        )
