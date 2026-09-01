"""Test script for Garmin Jr custom component."""
import json
import os
import py_compile
import sys
from typing import Any
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

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
    "homeassistant.helpers.event",
    "homeassistant.helpers.device_registry",
    "homeassistant.helpers.entity_platform",
    "homeassistant.helpers.update_coordinator",
    "homeassistant.helpers.selector",
    "homeassistant.data_entry_flow",
    "homeassistant.components",
    "homeassistant.components.sensor",
    "homeassistant.components.device_tracker",
    "homeassistant.components.switch",
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

ha_switch = sys.modules["homeassistant.components.switch"]
ha_switch.SwitchEntity = type("SwitchEntity", (), {})

ha_const = sys.modules["homeassistant.const"]
ha_const.PERCENTAGE = "%"
ha_const.UnitOfTime = type("UnitOfTime", (), {"MINUTES": "min"})

ha_tracker = sys.modules["homeassistant.components.device_tracker"]
ha_tracker.TrackerEntity = type("TrackerEntity", (), {})
ha_tracker.SourceType = type("SourceType", (), {"GPS": "gps"})

ha_coord = sys.modules["homeassistant.helpers.update_coordinator"]
ha_coord.CoordinatorEntity = type("CoordinatorEntity", (object,), {
    "__class_getitem__": lambda cls, item: cls,
    "__init__": lambda self, coordinator: setattr(self, "coordinator", coordinator),
    "async_write_ha_state": lambda self: None,
})
ha_coord.DataUpdateCoordinator = type("DataUpdateCoordinator", (object,), {"__class_getitem__": lambda cls, item: cls})
ha_coord.UpdateFailed = type("UpdateFailed", (Exception,), {})

ha_core = sys.modules["homeassistant.core"]
ha_core.callback = lambda f: f
ha_core.HomeAssistant = type("HomeAssistant", (), {})
ha_core.ServiceResponse = Any
ha_core.SupportsResponse = type("SupportsResponse", (), {"OPTIONAL": "optional", "ONLY": "only", "NONE": "none"})

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

    @patch("custom_components.garmin_jr.garmin_client.requests.post")
    @patch("custom_components.garmin_jr.garmin_client.requests.get")
    def test_fetch_all_data_vivokid_mocked(self, mock_requests_get, mock_requests_post):
        """Test parsing of Vivokid family, kids leaderboard, GPS trackpoints, and messages."""
        mock_requests_post.return_value.status_code = 200
        mock_requests_post.return_value.json = lambda: {"access_token": "mock_jr_tok", "expires_in": 21600}

        client = GarminJrClient(token_data={"di_token": "mock", "it_token": "mock_it"})
        client.client.di_token = "mock"
        client.client._token_expires_soon = MagicMock(return_value=False)

        class MockResponse:
            def __init__(self, status_code, json_data):
                self.status_code = status_code
                self._json = json_data

            def json(self):
                return self._json

        def mock_get(url, headers=None, params=None, timeout=10):
            if "v3/family/info" in url or "v2/family/info" in url:
                return MockResponse(200, {
                    "status": "OK",
                    "families": [{
                        "familyId": 12345678,
                        "name": "Test Family",
                        "guardians": [],
                        "kids": [{
                            "id": 98765432,
                            "name": "TestChild",
                            "deviceId": "98765432",
                            "hasLteDevice": True,
                        }]
                    }]
                })
            if "geofence/all" in url:
                return MockResponse(200, [])
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

        mock_requests_get.side_effect = mock_get
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

    @patch("custom_components.garmin_jr.garmin_client.requests.post")
    @patch("custom_components.garmin_jr.garmin_client.requests.get")
    def test_geofence_discovery_and_resolution(self, mock_requests_get, mock_requests_post):
        """Test Garmin geofence discovery and active safe zone resolution."""
        mock_requests_post.return_value.status_code = 200
        mock_requests_post.return_value.json = lambda: {"access_token": "mock_jr_tok", "expires_in": 21600}

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

        def mock_get(url, headers=None, params=None, timeout=10):
            if "v3/family/info" in url or "v2/family/info" in url:
                return MockResponse(200, {"status": "OK", "families": [{"familyId": 123, "name": "Family", "kids": [{"id": 98765432, "name": "Kid"}]}]})
            if "leaderboard/daily" in url:
                return MockResponse(200, {"kidStepsData": [{"id": 98765432, "displayName": "Kid"}]})
            return MockResponse(200, {})

        mock_requests_get.side_effect = mock_get
        client.client.connectapi = MagicMock(return_value=[])

        data = client.fetch_all_data()
        record = data["98765432"]

        self.assertEqual(record["garmin_safe_zone"], "Home")
        self.assertEqual(record["garmin_geofence_id"], "101")
        self.assertEqual(record["fix_type"], "Wfps")
        self.assertTrue(record["has_wifi"])
        self.assertEqual(len(record["geofences"]), 2)
        print("  [OK] Garmin Safe Zone discovery and status change resolution verified!")

    @patch("custom_components.garmin_jr.garmin_client.requests.post")
    @patch("custom_components.garmin_jr.garmin_client.requests.get")
    def test_audio_transcription_and_connect_id_matching(self, mock_requests_get, mock_requests_post):
        """Test audio voice message transcription extraction and connectId matching."""
        mock_requests_post.return_value.status_code = 200
        mock_requests_post.return_value.json = lambda: {"access_token": "mock_tok", "expires_in": 21600}

        client = GarminJrClient(token_data={"di_token": "mock", "it_token": "mock_it"})
        client.client.di_token = "mock"
        client.client._token_expires_soon = MagicMock(return_value=False)

        class MockResponse:
            def __init__(self, status_code, json_data):
                self.status_code = status_code
                self._json = json_data
            def json(self):
                return self._json

        def mock_get(url, headers=None, params=None, timeout=10):
            if "v3/family/info" in url or "v2/family/info" in url:
                return MockResponse(200, {
                    "status": "OK",
                    "families": [{
                        "familyId": 123,
                        "name": "Family",
                        "kids": [{
                            "id": 15839246,
                            "name": "Benjamin",
                            "connectId": 99887766,
                            "deviceId": "dev-123",
                        }]
                    }]
                })
            if "leaderboard/daily" in url:
                return MockResponse(200, {"kidStepsData": [{"id": 15839246, "displayName": "Benjamin"}]})
            return MockResponse(200, {})

        mock_requests_get.side_effect = mock_get
        client.client.connectapi = MagicMock(return_value=[])
        client.fetch_trackpoints = MagicMock(return_value=[])
        client.fetch_geofences = MagicMock(return_value=[])

        # Incoming voice audio message from child using connectId (userProfilePk)
        client.fetch_messages = MagicMock(return_value=[
            {
                "messageId": "msg-voice-99",
                "toUserProfilePk": 11223344,
                "fromUserProfilePk": 99887766,
                "senderDisplayName": "Benjamin",
                "mediaType": "Audio",
                "messageText": None,
                "transcription": "Open the garage door",
                "createdTimestamp": "2026-08-25T12:50:00Z"
            }
        ])

        data = client.fetch_all_data()
        self.assertIn("15839246", data)
        record = data["15839246"]

        self.assertEqual(record["last_message"], "Open the garage door")
        self.assertEqual(record["last_message_sender"], "Benjamin")
        self.assertEqual(record["last_message_media"], "Audio")
        self.assertEqual(len(record["new_messages"]), 1)
        print("  [OK] Audio message transcription extraction and connectId resolution verified!")

    def test_spot_plane_pipeline(self):
        """Test the plane spotting filter, ranking, and response formatting pipeline."""
        from custom_components.garmin_jr.plane_spotter import (
            filter_and_rank_planes,
            enrich_flight_details,
            format_bounce_response,
        )

        user_lat, user_lon = 46.7863, -71.2541
        mock_aircraft = [
            {
                "icao24": "c01234",
                "callsign": "ACA890",
                "type_code": "A223",
                "latitude": 46.7900,
                "longitude": -71.2500,
                "altitude_m": 3000.0,
                "altitude_ft": 9842.0,
            }
        ]

        ranked = filter_and_rank_planes(user_lat, user_lon, mock_aircraft, max_distance_km=30.0)
        self.assertEqual(len(ranked), 1)

        enriched = enrich_flight_details(ranked[0], language="fr")
        self.assertEqual(enriched["airline"], "Air Canada")
        self.assertEqual(enriched["model_name"], "Airbus A220-300")

        loc_info = {"latitude": user_lat, "longitude": user_lon, "zone_name": "Home"}
        msg = format_bounce_response(enriched, loc_info, language="fr")
        self.assertIn("Air Canada", msg)
        self.assertIn("890", msg)
        self.assertIn("Airbus A220-300", msg)
        self.assertIn("🛫", msg)
        self.assertLess(len(msg), 140)
        print("  [OK] Plane spotting filter, route enrichment, and Bounce watch formatting verified!")

    def test_ai_bridge_pipeline(self):
        """Test GarminBounceAiBridge session management, intent routing, and fallback."""
        from custom_components.garmin_jr.ai_bridge import GarminBounceAiBridge, ChildSession

        mock_hass = MagicMock()
        mock_hass.states.is_state.return_value = True

        bridge = GarminBounceAiBridge(mock_hass)
        session = bridge.get_session("15839246")

        # 1. Test flight context caching
        session.set_spotted_flight({
            "airline": "Air Transat",
            "callsign_iata": "TSC385",
            "model_name": "Airbus A321neo",
            "route": "Paris (CDG) ➔ Montréal (YUL)",
            "altitude_ft": 34000,
            "speed_kmh": 870,
        })
        ctx = session.get_spotted_flight_context()
        self.assertIsNotNone(ctx)
        self.assertIn("Air Transat", ctx)
        self.assertIn("A321neo", ctx)
        self.assertIn("34 000 pi", ctx)

        # 2. Test system prompt building
        kid_data = {"active_geofence_name": "Papa (Maison)"}
        prompt = bridge._build_system_prompt("Benjamin", kid_data, session)
        self.assertIn("10 ans", prompt)
        self.assertIn("chiffres réels", prompt)
        self.assertIn("140 CARACTÈRES", prompt)
        self.assertIn("Air Transat", prompt)

        # 3. Test fallback handler
        fallback_plane = bridge._fallback_handler("Quel est cet avion ?", "15839246", "Benjamin", kid_data, session)
        self.assertIsInstance(fallback_plane, str)

        fallback_garage = bridge._fallback_handler("Ouvre le garage stp", "15839246", "Benjamin", kid_data, session)
        self.assertIn("garage", fallback_garage.lower())

        fallback_chat = bridge._fallback_handler("Bonjour !", "15839246", "Benjamin", kid_data, session)
        self.assertEqual(fallback_chat, "Message bien reçu! 👍")
        print("  [OK] Strix Halo AI Bridge multi-turn context and action dispatching verified!")

    def test_school_mode_schedule_and_switch(self):
        """Test dynamic school mode detection, holiday bypass, dismissal calculation, and switch."""
        import datetime
        from custom_components.garmin_jr.coordinator import (
            get_child_school_mode_end_time,
            get_child_operating_mode,
        )
        from custom_components.garmin_jr.switch import GarminJrSchoolModeSwitch
        from custom_components.garmin_jr.sensor import GarminJrSensorEntity

        # 1. Active School Day during School Hours (e.g. Tuesday at 10:00 AM)
        tue_10am = datetime.datetime(2026, 9, 1, 10, 0, 0)  # Sep 1 2026 is Tuesday (weekday=1)
        active_child = {
            "school_mode": {
                "mode": "Restricted",
                "startTime": "08:00",
                "endTime": "15:00",
                "days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"],
            }
        }
        end_dt = get_child_school_mode_end_time(active_child, tue_10am)
        self.assertIsNotNone(end_dt)
        self.assertEqual(end_dt.hour, 15)
        self.assertEqual(end_dt.minute, 0)
        self.assertEqual(get_child_operating_mode(active_child, tue_10am), "school_mode")

        # 2. Dismissal after school hours (e.g. Tuesday at 3:05 PM / 15:05)
        tue_305pm = datetime.datetime(2026, 9, 1, 15, 5, 0)
        end_after = get_child_school_mode_end_time(active_child, tue_305pm)
        self.assertIsNone(end_after)
        self.assertEqual(get_child_operating_mode(active_child, tue_305pm), "active")

        # 3. Holiday / Vacation Mode: mode == 'Off'
        holiday_child = {
            "school_mode": {
                "mode": "Off",
                "startTime": "08:00",
                "endTime": "15:00",
                "days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"],
            }
        }
        end_holiday = get_child_school_mode_end_time(holiday_child, tue_10am)
        self.assertIsNone(end_holiday, "Holiday mode (Off) must NOT pause polling or enter school mode")
        self.assertEqual(get_child_operating_mode(holiday_child, tue_10am), "active")

        # 4. School Mode Switch Entity & Manual Override
        mock_coordinator = MagicMock()
        mock_coordinator.data = {"15839246": active_child}
        mock_coordinator.config_entry.entry_id = "test_entry"

        switch = GarminJrSchoolModeSwitch(mock_coordinator, "15839246")
        self.assertTrue(switch.is_on)

        # Toggle off for holiday
        import asyncio
        asyncio.run(switch.async_turn_off())
        self.assertFalse(switch.is_on)
        self.assertEqual(active_child["school_mode"]["mode"], "Off")
        mock_coordinator.set_child_school_mode_override.assert_called_with("15839246", False)
        mock_coordinator.reset_school_mode_pause.assert_called()

        # Check attributes
        attrs = switch.extra_state_attributes
        self.assertEqual(attrs["start_time"], "08:00")
        self.assertEqual(attrs["end_time"], "15:00")
        self.assertEqual(attrs["mode"], "Off")
        self.assertFalse(attrs["in_school_mode"])
        self.assertTrue(attrs["holiday_override"])

        # 5. School Mode Sensor Entity
        sensor = GarminJrSensorEntity(mock_coordinator, "15839246", "school_mode")
        self.assertEqual(sensor.native_value, "Off")
        print("  [OK] Dynamic School Mode schedule, holiday toggle, and dismissal calculations verified!")


if __name__ == "__main__":
    unittest.main()



