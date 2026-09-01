"""Interactive local test runner for Garmin Bounce AI Bridge."""
import sys
import types
from typing import Any

for mod in [
    "homeassistant",
    "homeassistant.config_entries",
    "homeassistant.core",
    "homeassistant.const",
    "homeassistant.exceptions",
    "homeassistant.helpers",
    "homeassistant.helpers.event",
    "homeassistant.helpers.device_registry",
    "homeassistant.helpers.entity_platform",
    "homeassistant.helpers.update_coordinator",
    "homeassistant.helpers.selector",
    "homeassistant.data_entry_flow",
    "homeassistant.components",
    "homeassistant.components.sensor",
    "homeassistant.components.device_tracker",
    "homeassistant.util",
    "homeassistant.util.dt",
    "voluptuous",
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
sys.modules["homeassistant.core"].SupportsResponse = type("SupportsResponse", (), {"OPTIONAL": "optional", "ONLY": "only", "NONE": "none"})

sys.modules["homeassistant.config_entries"].ConfigEntry = type("ConfigEntry", (), {})
sys.modules["homeassistant.config_entries"].ConfigFlow = type("ConfigFlow", (object,), {"__class_getitem__": lambda cls, item: cls, "__init_subclass__": lambda *args, **kwargs: None})
sys.modules["homeassistant.config_entries"].OptionsFlow = type("OptionsFlow", (), {})

sys.modules["homeassistant.helpers.update_coordinator"].CoordinatorEntity = type("CoordinatorEntity", (object,), {"__class_getitem__": lambda cls, item: cls})
sys.modules["homeassistant.helpers.update_coordinator"].DataUpdateCoordinator = type("DataUpdateCoordinator", (object,), {"__class_getitem__": lambda cls, item: cls})
sys.modules["homeassistant.helpers.update_coordinator"].UpdateFailed = type("UpdateFailed", (Exception,), {})
sys.modules["homeassistant.exceptions"].ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})

from unittest.mock import MagicMock
from custom_components.garmin_jr.ai_bridge import GarminBounceAiBridge

# Setup mock HA instance with user's Home coordinates
mock_hass = MagicMock()
mock_hass.config.latitude = 46.7863171
mock_hass.config.longitude = -71.2540787

# Simulated child data at Home (Papa safe zone)
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

def run_test_turn(user_message: str):
    print("\n" + "=" * 60)
    print(f"👦 Benjamin: \"{user_message}\"")
    print("-" * 60)
    
    reply = bridge.process_incoming_message(
        child_id="15839246",
        child_name="Benjamin",
        incoming_text=user_message,
        child_data=mock_kid_data,
    )
    
    print(f"🤖 Assistant Reply ({len(reply)} chars):")
    print(reply)
    print("=" * 60)
    return reply

if __name__ == "__main__":
    test_queries = [
        "Quel est cet avion ?",
        "C'est gros comment et combien de passagers il transporte ?",
        "Il vole à quelle vitesse et à quelle altitude ?",
        "Pourquoi il laisse une traînée blanche derrière lui ?",
        "Ouvre le garage svp",
        "Merci beaucoup !",
    ]
    
    if len(sys.argv) > 1:
        custom_q = " ".join(sys.argv[1:])
        run_test_turn(custom_q)
    else:
        for q in test_queries:
            run_test_turn(q)
