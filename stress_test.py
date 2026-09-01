import sys
import types
import time
from typing import Any

# Minimal HA mocks
for mod in [
    "homeassistant", "homeassistant.config_entries", "homeassistant.core",
    "homeassistant.const", "homeassistant.exceptions", "homeassistant.helpers",
    "homeassistant.helpers.event", "homeassistant.helpers.device_registry",
    "homeassistant.helpers.entity_platform", "homeassistant.helpers.update_coordinator",
    "homeassistant.helpers.selector", "homeassistant.data_entry_flow",
    "homeassistant.components", "homeassistant.components.sensor",
    "homeassistant.components.device_tracker", "homeassistant.util",
    "homeassistant.util.dt", "voluptuous",
]:
    if mod not in sys.modules:
        m = types.ModuleType(mod)
        m.__path__ = []
        sys.modules[mod] = m

import datetime as _dt
sys.modules["homeassistant.util.dt"].now = lambda *args, **kwargs: _dt.datetime.now()
sys.modules["homeassistant.helpers.event"].async_track_time_interval = lambda *args, **kwargs: (lambda: None)
sys.modules["homeassistant.core"].CALLBACK_TYPE = Any
sys.modules["homeassistant.core"].callback = lambda f: f
sys.modules["homeassistant.core"].HomeAssistant = type("HomeAssistant", (), {})
sys.modules["homeassistant.core"].ServiceResponse = Any
sys.modules["homeassistant.core"].SupportsResponse = type("SupportsResponse", (), {"OPTIONAL": "optional"})
sys.modules["homeassistant.config_entries"].ConfigEntry = type("ConfigEntry", (), {})
sys.modules["homeassistant.config_entries"].ConfigFlow = type("ConfigFlow", (object,), {"__class_getitem__": lambda cls, item: cls, "__init_subclass__": lambda *args, **kwargs: None})
sys.modules["homeassistant.config_entries"].OptionsFlow = type("OptionsFlow", (), {})
sys.modules["homeassistant.helpers.update_coordinator"].CoordinatorEntity = type("CoordinatorEntity", (object,), {"__class_getitem__": lambda cls, item: cls})
sys.modules["homeassistant.helpers.update_coordinator"].DataUpdateCoordinator = type("DataUpdateCoordinator", (object,), {"__class_getitem__": lambda cls, item: cls})
sys.modules["homeassistant.helpers.update_coordinator"].UpdateFailed = type("UpdateFailed", (Exception,), {})
sys.modules["homeassistant.exceptions"].ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})

from unittest.mock import MagicMock
from custom_components.garmin_jr.ai_bridge import GarminBounceAiBridge

mock_hass = MagicMock()
mock_hass.config.latitude = 46.7863171
mock_hass.config.longitude = -71.2540787

mock_kid_data = {
    "child_id": "15839246",
    "child_name": "Benjamin",
    "active_geofence_name": "Papa (Maison)",
    "garmin_safe_zone": "Papa",
    "latitude": 46.7863171,
    "longitude": -71.2540787,
    "matched_ha_zone": "Home",
}

bridge = GarminBounceAiBridge(mock_hass)

print("Starting Rapid 3-Request Stress Test (5s interval)...")
test_phrases = [
    "Quel est cet avion ?",
    "Quel est l'avion qui passe au dessus de moi ?",
    "C'est quoi cet avion ?",
]

for i, phrase in enumerate(test_phrases, start=1):
    print(f"\n[{_dt.datetime.now().strftime('%H:%M:%S')}] --- Request {i}/3: \"{phrase}\" ---")
    t0 = time.time()
    try:
        reply = bridge.process_incoming_message(
            child_id="15839246",
            child_name="Benjamin",
            incoming_text=phrase,
            child_data=mock_kid_data,
        )
        dt = time.time() - t0
        print(f"[{_dt.datetime.now().strftime('%H:%M:%S')}] Status: SUCCESS in {dt:.2f}s | Length: {len(reply)} chars")
        print("Reply:")
        print(reply)
    except Exception as err:
        print(f"[{_dt.datetime.now().strftime('%H:%M:%S')}] Status: FAILED with error: {err}")
    
    if i < len(test_phrases):
        print(f"Waiting 5 seconds before next request...")
        time.sleep(5)

print("\nStress Test Completed Successfully!")
