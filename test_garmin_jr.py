"""Test script for Garmin Jr custom component."""
import json
import os
import py_compile
import sys
from typing import Any
import unittest
from unittest.mock import MagicMock

# Verify compilation of all Python files in the component
print("Checking Python syntax compilation...")
component_dir = os.path.join(os.path.dirname(__file__), "custom_components", "garmin_jr")
py_files = [f for f in os.listdir(component_dir) if f.endswith(".py")]

for py_file in py_files:
    full_path = os.path.join(component_dir, py_file)
    try:
        py_compile.compile(full_path, doraise=True)
        print(f"  [OK] {py_file} compiled successfully")
    except Exception as e:
        print(f"  [FAIL] {py_file} syntax error: {e}")
        sys.exit(1)

# Validate JSON files
for json_file in ["manifest.json", "strings.json", "translations/en.json"]:
    full_path = os.path.join(component_dir, json_file)
    with open(full_path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
            print(f"  [OK] {json_file} is valid JSON")
        except Exception as e:
            print(f"  [FAIL] {json_file} JSON error: {e}")
            sys.exit(1)

# Mock homeassistant modules so all component platforms can be imported and executed
import types
for mod in [
    "homeassistant",
    "homeassistant.config_entries",
    "homeassistant.core",
    "homeassistant.const",
    "homeassistant.exceptions",
    "homeassistant.helpers",
    "homeassistant.helpers.device_registry",
    "homeassistant.helpers.entity_platform",
    "homeassistant.helpers.update_coordinator",
    "homeassistant.helpers.selector",
    "homeassistant.data_entry_flow",
    "homeassistant.components",
    "homeassistant.components.sensor",
    "homeassistant.components.device_tracker",
    "voluptuous",
]:
    if mod not in sys.modules:
        m = types.ModuleType(mod)
        m.__path__ = []
        sys.modules[mod] = m

ha_flow = sys.modules["homeassistant.data_entry_flow"]
ha_flow.FlowResult = Any
ha_flow.FlowHandler = type("FlowHandler", (), {})

ha_exceptions = sys.modules["homeassistant.exceptions"]
ha_exceptions.ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})

# Mock common attributes
ha_platform = sys.modules["homeassistant.helpers.entity_platform"]
ha_platform.AddEntitiesCallback = Any
ha_sensor = sys.modules["homeassistant.components.sensor"]
ha_sensor.SensorEntity = type("SensorEntity", (), {})
ha_sensor.SensorDeviceClass = type("SensorDeviceClass", (), {"DURATION": "duration", "BATTERY": "battery", "TIMESTAMP": "timestamp"})
ha_sensor.SensorStateClass = type("SensorStateClass", (), {"TOTAL_INCREASING": "total_increasing", "MEASUREMENT": "measurement"})

ha_const = sys.modules["homeassistant.const"]
ha_const.PERCENTAGE = "%"
ha_const.UnitOfTime = type("UnitOfTime", (), {"MINUTES": "min"})

ha_tracker = sys.modules["homeassistant.components.device_tracker"]
ha_tracker.TrackerEntity = type("TrackerEntity", (), {})
ha_tracker.SourceType = type("SourceType", (), {"GPS": "gps"})

ha_coord = sys.modules["homeassistant.helpers.update_coordinator"]
ha_coord.CoordinatorEntity = type("CoordinatorEntity", (object,), {"__class_getitem__": lambda cls, item: cls})
ha_coord.DataUpdateCoordinator = type("DataUpdateCoordinator", (object,), {"__class_getitem__": lambda cls, item: cls})
ha_coord.UpdateFailed = type("UpdateFailed", (Exception,), {})

ha_core = sys.modules["homeassistant.core"]
ha_core.callback = lambda f: f
ha_core.HomeAssistant = type("HomeAssistant", (), {})

ha_entries = sys.modules["homeassistant.config_entries"]
ha_entries.ConfigEntry = type("ConfigEntry", (), {})
ha_entries.ConfigFlow = type("ConfigFlow", (object,), {"__class_getitem__": lambda cls, item: cls, "__init_subclass__": lambda *args, **kwargs: None})
ha_entries.OptionsFlow = type("OptionsFlow", (), {})

ha_dev_reg = sys.modules["homeassistant.helpers.device_registry"]
ha_dev_reg.DeviceInfo = type("DeviceInfo", (), {"__init__": lambda *args, **kwargs: None})

ha_sel = sys.modules["homeassistant.helpers.selector"]
ha_sel.SelectOptionDict = lambda **kwargs: kwargs
ha_sel.SelectSelector = lambda *args, **kwargs: kwargs
ha_sel.SelectSelectorConfig = type("SelectSelectorConfig", (), {"__init__": lambda *args, **kwargs: None})
ha_sel.SelectSelectorMode = type("SelectSelectorMode", (), {"DROPDOWN": "dropdown"})
ha_sel.NumberSelector = lambda *args, **kwargs: kwargs
ha_sel.NumberSelectorConfig = type("NumberSelectorConfig", (), {"__init__": lambda *args, **kwargs: None})
ha_sel.NumberSelectorMode = type("NumberSelectorMode", (), {"BOX": "box"})
ha_sel.TextSelector = lambda *args, **kwargs: kwargs
ha_sel.TextSelectorConfig = type("TextSelectorConfig", (), {"__init__": lambda *args, **kwargs: None})
ha_sel.TextSelectorType = type("TextSelectorType", (), {"EMAIL": "email", "PASSWORD": "password", "TEXT": "text"})

vol = sys.modules["voluptuous"]
vol.Schema = lambda *args, **kwargs: kwargs
vol.Required = lambda *args, **kwargs: args[0] if args else "key"
vol.Optional = lambda *args, **kwargs: args[0] if args else "key"

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "custom_components", "garmin_jr"))

print("Checking Python module execution and imports...")
import custom_components.garmin_jr.const
import custom_components.garmin_jr.garmin_client
import custom_components.garmin_jr.coordinator
import custom_components.garmin_jr.device_tracker
import custom_components.garmin_jr.sensor
import custom_components.garmin_jr.config_flow
import custom_components.garmin_jr
print("  [OK] All modules imported and executed successfully!")

from garmin_client import GarminJrClient

class TestGarminJrClient(unittest.TestCase):
    """Test suite for GarminJrClient."""

    def test_token_dump_and_load(self):
        """Test token serialization."""
        tokens = {
            "di_token": "mock_access_token",
            "di_refresh_token": "mock_refresh_token",
            "di_client_id": "mock_client_id",
            "it_token": "mock_it_token",
            "it_refresh_token": "mock_it_refresh",
            "it_expires_at": 1787499999.0,
        }
        client = GarminJrClient(token_data=tokens)
        dumped = client.get_token_data()
        self.assertEqual(dumped["di_token"], "mock_access_token")
        self.assertEqual(dumped["di_refresh_token"], "mock_refresh_token")
        self.assertEqual(dumped["it_token"], "mock_it_token")
        self.assertEqual(dumped["it_refresh_token"], "mock_it_refresh")

    def test_fetch_all_data_vivokid_mocked(self):
        """Test parsing of Vivokid family, kids leaderboard, GPS trackpoints, and messages."""
        client = GarminJrClient(token_data={"di_token": "mock", "it_token": "mock_it"})
        client.client.di_token = "mock"
        client.client._token_expires_soon = MagicMock(return_value=False)

        class MockResponse:
            def __init__(self, status_code, json_data):
                self.status_code = status_code
                self._json = json_data

            def json(self):
                return self._json

        def mock_get(url, headers=None, timeout=10):
            if "v2/family/info" in url:
                return MockResponse(200, {
                    "status": "OK",
                    "family": {
                        "familyId": 12345678,
                        "name": "Test Family",
                        "guardians": [],
                        "kids": []
                    }
                })
            if "leaderboard/daily" in url:
                return MockResponse(200, {
                    "kidStepsData": [
                        {
                            "id": 98765432,
                            "displayName": "TestChild",
                            "steps": 6250,
                            "lastSyncDate": "2026-08-23T10:30:00.000"
                        }
                    ]
                })
            if "summary/kid" in url:
                return MockResponse(200, {
                    "stepsGoal": 7500,
                    "stepsRecord": 25000,
                    "lastSyncDate": 1787448776455
                })
            if "personalrecords" in url:
                return MockResponse(200, {
                    "stepsRecord": 25000,
                    "activeMinuteRecord": 180
                })
            return MockResponse(404, {})

        client.client._api_session.get = MagicMock(side_effect=mock_get)
        client.client.connectapi = MagicMock(return_value=[])

        # Mock GPS trackpoints and messages
        client.fetch_trackpoints = MagicMock(return_value=[
            {
                "latitude": 45.5017,
                "longitude": -73.5673,
                "accuracy": 8,
                "timestamp": "2026-08-23T12:00:00Z"
            }
        ])
        client.fetch_messages = MagicMock(return_value=[
            {
                "messageId": "msg-12345",
                "toUserProfilePk": 98765432,
                "fromUserProfilePk": 11111111,
                "senderDisplayName": "Guardian",
                "messageText": "Dinner is ready!",
                "mediaType": "Text",
                "createdTimestamp": "2026-08-23T12:05:00Z"
            }
        ])

        data = client.fetch_all_data()
        self.assertIn("98765432", data)
        record = data["98765432"]

        self.assertEqual(record["child_name"], "TestChild")
        self.assertEqual(record["model"], "Garmin Bounce")
        self.assertEqual(record["steps"], 6250)
        self.assertEqual(record["daily_step_goal"], 7500)
        self.assertEqual(record["steps_record"], 25000)
        self.assertEqual(record["active_minutes_record"], 180)
        self.assertEqual(record["family_name"], "Test Family")
        self.assertEqual(record["latitude"], 45.5017)
        self.assertEqual(record["longitude"], -73.5673)
        self.assertEqual(record["gps_accuracy"], 8)
        self.assertEqual(record["last_message"], "Dinner is ready!")
        self.assertEqual(record["last_message_sender"], "Guardian")
        print("  [OK] Vivokid kids, GPS, and messaging telemetry verified!")

    def test_geofence_discovery_and_resolution(self):
        """Test Garmin geofence discovery and active safe zone resolution."""
        client = GarminJrClient(token_data={"di_token": "mock", "it_token": "mock_it"})
        client.client.di_token = "mock"
        client.client._token_expires_soon = MagicMock(return_value=False)

        client.fetch_geofences = MagicMock(return_value=[
            {
                "id": 101,
                "name": "Home",
                "latitude": 45.5000,
                "longitude": -73.5600,
                "radius": 150,
                "wifi_ssid": "HomeWiFi",
                "kid_ids": [98765432],
            },
            {
                "id": 102,
                "name": "School",
                "latitude": 45.5100,
                "longitude": -73.5700,
                "radius": 200,
                "wifi_ssid": None,
                "kid_ids": [98765432],
            },
        ])

        client.fetch_trackpoints = MagicMock(return_value=[
            {
                "latitude": 45.5002,
                "longitude": -73.5601,
                "accuracy": 20,
                "timestamp": "2026-08-23T14:00:00Z",
                "fixType": "Wfps",
                "familyPointData": {
                    "statusChanges": [
                        {"deviceState": "GeofenceEnter", "geofenceId": 101}
                    ]
                }
            }
        ])

        client.fetch_messages = MagicMock(return_value=[])

        class MockResponse:
            def __init__(self, status_code, json_data):
                self.status_code = status_code
                self._json = json_data
            def json(self):
                return self._json

        def mock_get(url, headers=None, timeout=10):
            if "v2/family/info" in url:
                return MockResponse(200, {"status": "OK", "family": {"familyId": 123, "name": "Family"}})
            if "leaderboard/daily" in url:
                return MockResponse(200, {"kidStepsData": [{"id": 98765432, "displayName": "Kid"}]})
            return MockResponse(200, {})

        client.client._api_session.get = MagicMock(side_effect=mock_get)
        client.client.connectapi = MagicMock(return_value=[])

        data = client.fetch_all_data()
        record = data["98765432"]

        self.assertEqual(record["garmin_safe_zone"], "Home")
        self.assertEqual(record["garmin_geofence_id"], "101")
        self.assertEqual(record["fix_type"], "Wfps")
        self.assertTrue(record["has_wifi"])
        self.assertEqual(len(record["geofences"]), 2)
        print("  [OK] Garmin Safe Zone discovery and status change resolution verified!")

if __name__ == "__main__":
    unittest.main()


