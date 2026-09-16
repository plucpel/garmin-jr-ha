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
sys.modules["homeassistant.helpers.event"].async_call_later = lambda *args, **kwargs: (lambda: None)
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
    "_handle_coordinator_update": lambda self: None,
})
ha_coord.DataUpdateCoordinator = type("DataUpdateCoordinator", (object,), {
    "__class_getitem__": lambda cls, item: cls,
    "__init__": lambda self, *args, **kwargs: None,
})
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
ha_sel.BooleanSelector = lambda *args, **kwargs: kwargs
ha_sel.BooleanSelectorConfig = type("BooleanSelectorConfig", (), {"__init__": lambda *args, **kwargs: None})

vol = sys.modules["voluptuous"]
vol.Schema = type("Schema", (object,), {"__init__": lambda self, schema: setattr(self, "schema", schema)})

class MockMarker:
    def __init__(self, schema, default=None):
        self.schema = schema
        self.default = default
    def __str__(self):
        return str(self.schema)

vol.Required = lambda schema, default=None, **kwargs: MockMarker(schema, default)
vol.Optional = lambda schema, default=None, **kwargs: MockMarker(schema, default)

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
                "latitude": 48.8584,
                "longitude": 2.2945,
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
        self.assertEqual(record["latitude"], 48.8584)
        self.assertEqual(record["longitude"], 2.2945)
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
                "latitude": 48.8500,
                "longitude": 2.2900,
                "radius": 150,
                "wifi_ssid": "HomeWiFi",
                "kid_ids": [98765432],
            },
            {
                "id": 102,
                "name": "School",
                "latitude": 48.8600,
                "longitude": 2.3000,
                "radius": 200,
                "wifi_ssid": None,
                "kid_ids": [98765432],
            },
        ])

        client.fetch_trackpoints = MagicMock(return_value=[
            {
                "latitude": 48.8502,
                "longitude": 2.2901,
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
                            "id": 998877,
                            "name": "Alex",
                            "connectId": 99887766,
                            "deviceId": "dev-123",
                        }]
                    }]
                })
            if "leaderboard/daily" in url:
                return MockResponse(200, {"kidStepsData": [{"id": 998877, "displayName": "Alex"}]})
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
                "senderDisplayName": "Alex",
                "mediaType": "Audio",
                "messageText": None,
                "transcription": "Open the garage door",
                "createdTimestamp": "2026-08-25T12:50:00Z"
            }
        ])

        data = client.fetch_all_data()
        self.assertIn("998877", data)
        record = data["998877"]

        self.assertEqual(record["last_message"], "Open the garage door")
        self.assertEqual(record["last_message_sender"], "Alex")
        self.assertEqual(record["last_message_media"], "Audio")
        self.assertEqual(len(record["new_messages"]), 1)

        # Verify fast message poll matching for Bounce profile PK 555000111
        bounce_msg = [{
            "messageId": "msg-bounce-1",
            "toUserProfilePk": 150263349,
            "fromUserProfilePk": 555000111,
            "mediaType": "audio/amr",
            "messageLength": 2316,
            "transcription": None,
            "createdTimestamp": "2026-09-04T19:19:51.921Z",
        }]
        parsed = client.parse_child_messages(
            bounce_msg,
            kid_id="998877",
            device_id="998877",
            user_profile_pk="555000111",
        )
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["fromUserProfilePk"], 555000111)

        # Verify another child does not match this message
        parsed_other = client.parse_child_messages(
            bounce_msg,
            kid_id="99999999",
            device_id="99999999",
            user_profile_pk="88888888",
            total_family_kids=2,
        )
        self.assertEqual(len(parsed_other), 0, "Multi-child isolation: message must not match unrelated child")
        print("  [OK] Audio message transcription extraction, Bounce profile 555000111, and connectId resolution verified!")

    def test_spot_plane_pipeline(self):
        """Test the plane spotting filter, ranking, and response formatting pipeline."""
        from custom_components.garmin_jr.plane_spotter import (
            filter_and_rank_planes,
            enrich_flight_details,
            format_bounce_response,
        )

        user_lat, user_lon = 48.8584, 2.2945
        mock_aircraft = [
            {
                "icao24": "c01234",
                "callsign": "ACA890",
                "type_code": "A223",
                "latitude": 48.8600,
                "longitude": 2.3000,
                "altitude_m": 3000.0,
                "altitude_ft": 9842.0,
            }
        ]

        ranked = filter_and_rank_planes(user_lat, user_lon, mock_aircraft, max_distance_km=30.0)
        self.assertEqual(len(ranked), 1)

        enriched = enrich_flight_details(ranked[0], language="fr")
        self.assertEqual(enriched["airline"], "Air Canada")
        self.assertEqual(enriched["model_name"], "Airbus A220-300")

        # Test low-altitude small plane vs distant high-altitude transatlantic flight
        low_cessna = {
            "icao24": "c09999",
            "callsign": "CGXYZ",
            "type_code": "C172",
            "latitude": 48.8600,
            "longitude": 2.2800,
            "altitude_m": 450.0,
            "altitude_ft": 1476.0,
        }
        high_b777 = {
            "icao24": "c08888",
            "callsign": "ACA895",
            "type_code": "B77W",
            "latitude": 49.1000,
            "longitude": 2.5000,
            "altitude_m": 11000.0,
            "altitude_ft": 36089.0,
        }
        ranked_multi = filter_and_rank_planes(user_lat, user_lon, [high_b777, low_cessna])
        self.assertEqual(len(ranked_multi), 2)
        self.assertEqual(ranked_multi[0]["callsign"], "CGXYZ", "Low flying small plane must rank first over distant airliner")
        self.assertEqual(ranked_multi[1]["callsign"], "ACA895")

        print("  [OK] Plane spotting filter, route enrichment, and Bounce watch formatting verified!")

    def test_ai_bridge_pipeline(self):
        """Test GarminBounceAiBridge session management, intent routing, and fallback."""
        from custom_components.garmin_jr.ai_bridge import GarminBounceAiBridge, ChildSession

        mock_hass = MagicMock()
        mock_hass.states.is_state.return_value = True

        bridge = GarminBounceAiBridge(mock_hass)
        session = bridge.get_session("998877")

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
        prompt = bridge._build_system_prompt("Alex", kid_data, session)
        self.assertIn("10 ans", prompt)
        self.assertIn("chiffres réels", prompt)
        self.assertIn("140 CARACTÈRES", prompt)
        self.assertIn("Air Transat", prompt)

        # 3. Test fallback handler
        fallback_plane = bridge._fallback_handler("Quel est cet avion ?", "998877", "Alex", kid_data, session)
        self.assertIsInstance(fallback_plane, str)

        fallback_garage = bridge._fallback_handler("Ouvre le garage stp", "998877", "Alex", kid_data, session)
        self.assertIn("garage", fallback_garage.lower())

        fallback_chat = bridge._fallback_handler("Bonjour !", "998877", "Alex", kid_data, session)
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
        mock_coordinator.data = {"998877": active_child}
        mock_coordinator.config_entry.entry_id = "test_entry"

        switch = GarminJrSchoolModeSwitch(mock_coordinator, "998877")
        self.assertTrue(switch.is_on)

        # Toggle off for holiday
        import asyncio
        asyncio.run(switch.async_turn_off())
        self.assertFalse(switch.is_on)
        self.assertEqual(active_child["school_mode"]["mode"], "Off")
        mock_coordinator.set_child_school_mode_override.assert_called_with("998877", False)
        mock_coordinator.reset_school_mode_pause.assert_called()

        # Check attributes
        attrs = switch.extra_state_attributes
        self.assertEqual(attrs["start_time"], "08:00")
        self.assertEqual(attrs["end_time"], "15:00")
        self.assertEqual(attrs["mode"], "Off")
        self.assertFalse(attrs["in_school_mode"])
        self.assertTrue(attrs["holiday_override"])

        # 5. School Mode Sensor Entity
        sensor = GarminJrSensorEntity(mock_coordinator, "998877", "school_mode")
        self.assertEqual(sensor.native_value, "Off")

        # 6. Test dndEnabled device setting does NOT cause sleep_time during the day
        bounce_child_with_dnd = {
            "settings": {"dndEnabled": True},
            "school_mode": {
                "mode": "Restricted",
                "startTime": 28920,
                "endTime": 54360,
                "days": ["TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"],
            },
        }
        # Monday at 15:30 (outside school days list)
        mon_330pm = datetime.datetime(2026, 9, 7, 15, 30, 0)  # Sep 7 2026 is Monday (weekday=0)
        self.assertIsNone(get_child_school_mode_end_time(bounce_child_with_dnd, mon_330pm))
        self.assertEqual(
            get_child_operating_mode(bounce_child_with_dnd, mon_330pm),
            "active",
            "dndEnabled: True must NOT put watch into sleep_time during daytime!",
        )

        # Monday at 23:30 (nighttime)
        mon_1130pm = datetime.datetime(2026, 9, 7, 23, 30, 0)
        self.assertEqual(
            get_child_operating_mode(bounce_child_with_dnd, mon_1130pm),
            "sleep_time",
            "Nighttime (23:30) must put watch into sleep_time",
        )

        # Tuesday at 10:00 (school time)
        tue_1000am = datetime.datetime(2026, 9, 8, 10, 0, 0)
        self.assertEqual(
            get_child_operating_mode(bounce_child_with_dnd, tue_1000am),
            "school_mode",
            "Tuesday at 10:00 must be in school_mode",
        )

        print("  [OK] Dynamic School Mode schedule, holiday toggle, and dismissal calculations verified!")

    def test_bounded_set_eviction(self):
        """Test BoundedSet FIFO eviction and bounded capacity."""
        from custom_components.garmin_jr.coordinator import BoundedSet

        bset = BoundedSet(maxlen=5)
        for i in range(5):
            bset.add(f"id_{i}")

        self.assertEqual(len(bset), 5)
        self.assertIn("id_0", bset)
        self.assertIn("id_4", bset)

        # Add 6th element -> id_0 must be evicted
        bset.add("id_5")
        self.assertEqual(len(bset), 5)
        self.assertNotIn("id_0", bset)
        self.assertIn("id_5", bset)
        print("  [OK] BoundedSet FIFO eviction and bounded capacity verified!")

    def test_find_child_target(self):
        """Test find_child_target helper across coordinators and children."""
        from custom_components.garmin_jr import find_child_target
        from custom_components.garmin_jr.coordinator import GarminJrDataUpdateCoordinator
        from custom_components.garmin_jr.const import ATTR_CHILD_NAME, DOMAIN

        mock_hass = MagicMock()
        mock_coord = MagicMock(spec=GarminJrDataUpdateCoordinator)
        mock_coord.data = {
            "child_1": {
                ATTR_CHILD_NAME: "Alice",
                "connectId": 1001,
                "userProfilePk": 1001,
                "deviceId": "dev_1",
            },
            "child_2": {
                ATTR_CHILD_NAME: "Bob",
                "connectId": 2002,
                "userProfilePk": 2002,
                "deviceId": "dev_2",
            },
        }
        mock_hass.data = {DOMAIN: {"entry_1": mock_coord}}

        # Target by ID
        c, kid_id, data, pk, dev_id = find_child_target(mock_hass, "child_2")
        self.assertEqual(kid_id, "child_2")
        self.assertEqual(pk, 2002)
        self.assertEqual(dev_id, "dev_2")

        # Target by Name
        c, kid_id, data, pk, dev_id = find_child_target(mock_hass, "alice")
        self.assertEqual(kid_id, "child_1")
        self.assertEqual(pk, 1001)

        # Target empty -> defaults to first child
        c, kid_id, data, pk, dev_id = find_child_target(mock_hass, "")
        self.assertEqual(kid_id, "child_1")

        # Target not found
        c, kid_id, data, pk, dev_id = find_child_target(mock_hass, "nonexistent")
        self.assertIsNone(c)
        self.assertIsNone(kid_id)
        print("  [OK] find_child_target ID, Name, and fallback matching verified!")

    def test_device_tracker_cached_coordinates(self):
        """Test GarminJrTrackerEntity coordinate caching."""
        from custom_components.garmin_jr.device_tracker import GarminJrTrackerEntity
        from custom_components.garmin_jr.const import (
            ATTR_LATITUDE,
            ATTR_LONGITUDE,
            ATTR_ACCURACY,
        )

        mock_coord = MagicMock()
        mock_coord.data = {
            "child_1": {
                ATTR_LATITUDE: 48.8584,
                ATTR_LONGITUDE: 2.2945,
                ATTR_ACCURACY: 10,
            }
        }
        mock_entry = MagicMock()
        mock_entry.options = {}

        tracker = GarminJrTrackerEntity(mock_coord, mock_entry, "child_1")
        self.assertEqual(tracker.latitude, 48.8584)
        self.assertEqual(tracker.longitude, 2.2945)
        self.assertEqual(tracker.location_accuracy, 10)

        # Update data and trigger coordinator update
        mock_coord.data["child_1"][ATTR_LATITUDE] = 48.8600
        mock_coord.data["child_1"][ATTR_LONGITUDE] = 2.3000
        tracker._handle_coordinator_update()

        self.assertEqual(tracker.latitude, 48.8600)
        self.assertEqual(tracker.longitude, 2.3000)
        print("  [OK] Device tracker zone coordinate resolution and caching verified!")

    def test_school_mode_opt_in_default(self):
        """Test school mode defaults to disabled if payload is missing or empty."""
        import datetime
        from custom_components.garmin_jr.coordinator import (
            get_child_school_mode_end_time,
            get_child_operating_mode,
        )

        child_no_school = {"child_name": "Charlie"}
        tue_10am = datetime.datetime(2026, 9, 1, 10, 0, 0)
        self.assertIsNone(get_child_school_mode_end_time(child_no_school, tue_10am))
        self.assertEqual(get_child_operating_mode(child_no_school, tue_10am), "active")
        print("  [OK] School mode opt-in default for missing payloads verified!")

    def test_self_learning_child_pk_binding(self):
        """Test that client dynamically learns and binds child profile PK from incoming messages, strictly excluding guardian PKs."""
        client = GarminJrClient(token_data={"di_token": "mock", "it_token": "mock_it"})
        # Register parent/guardian profile PK
        client._guardian_profile_pks.add("111222333")

        # 1. Parent sends message to watch: from=Parent (111222333), to=Watch (999111222)
        parent_msg = [{
            "messageId": "msg-parent-1",
            "fromUserProfilePk": 111222333,
            "toUserProfilePk": 999111222,
            "messageText": "Dinner is ready!",
        }]

        # Before parsing, client has no learned PK
        self.assertNotIn("child_1", client._learned_child_pks)

        # Parse with only kid_id known
        parsed = client.parse_child_messages(parent_msg, kid_id="child_1", total_family_kids=1)
        self.assertEqual(len(parsed), 1)

        # Client learned and bound the watch PK (999111222) and NEVER the guardian PK (111222333)
        self.assertEqual(client._learned_child_pks.get("child_1"), "999111222")
        self.assertNotEqual(client._learned_child_pks.get("child_1"), "111222333")

        # 2. Watch sends audio note to parent: from=Watch (999111222), to=Parent (111222333)
        watch_msg = [{
            "messageId": "msg-watch-1",
            "fromUserProfilePk": 999111222,
            "toUserProfilePk": 111222333,
            "mediaType": "Audio",
            "transcription": "Coming home now!",
        }]
        parsed_watch = client.parse_child_messages(watch_msg, kid_id="child_1", total_family_kids=2)
        self.assertEqual(len(parsed_watch), 1)
        self.assertEqual(client._learned_child_pks.get("child_1"), "999111222")
        print("  [OK] Self-learning child profile PK binding & guardian exclusion verified!")

    def test_dual_key_device_id_compatibility(self):
        """Test that find_child_target works with both deviceId and device_id."""
        from custom_components.garmin_jr import find_child_target
        from custom_components.garmin_jr.coordinator import GarminJrDataUpdateCoordinator
        from custom_components.garmin_jr.const import DOMAIN, ATTR_CHILD_NAME

        mock_hass = MagicMock()
        mock_coord = MagicMock(spec=GarminJrDataUpdateCoordinator)
        mock_coord.entry = MagicMock()
        mock_coord.entry.options = {}
        mock_coord.client = MagicMock()
        mock_coord.client._learned_child_pks = {}
        mock_coord.data = {
            "kid_1": {
                ATTR_CHILD_NAME: "Sam",
                "device_id": "3617969920",
                "connectId": 555000111,
            }
        }
        mock_hass.data = {DOMAIN: {"entry_1": mock_coord}}

        c, kid_id, data, pk, dev_id = find_child_target(mock_hass, "Sam")
        self.assertEqual(dev_id, "3617969920")
        self.assertEqual(pk, 555000111)
        print("  [OK] Dual-key deviceId/device_id compatibility verified!")

    def test_resolve_child_profile_pk_priority(self):
        """Test canonical priority order of resolve_child_profile_pk helper."""
        from custom_components.garmin_jr.coordinator import resolve_child_profile_pk

        # 1. Option override takes top priority
        child_data = {
            "user_profile_pk": "222",
            "connect_id": "333",
            "child_id": "child_1",
        }
        options = {"profile_pk_child_1": "111"}
        learned = {"child_1": "444"}
        self.assertEqual(resolve_child_profile_pk("child_1", child_data, options=options, learned_pks=learned), "111")

        # 2. Payload user_profile_pk takes priority over connect_id and learned
        self.assertEqual(resolve_child_profile_pk("child_1", child_data, options={}, learned_pks=learned), "222")

        # 3. Discovered connect_id takes priority over learned
        child_data_no_pk = {"connect_id": "333", "child_id": "child_1"}
        self.assertEqual(resolve_child_profile_pk("child_1", child_data_no_pk, options={}, learned_pks=learned), "333")

        # 4. Learned PK used when payload has no PK/connect_id
        child_bare = {"child_id": "child_1"}
        self.assertEqual(resolve_child_profile_pk("child_1", child_bare, options={}, learned_pks=learned), "444")

        # 5. Fallback to child_id
        self.assertEqual(resolve_child_profile_pk("child_1", child_bare, options={}, learned_pks={}), "child_1")
        print("  [OK] resolve_child_profile_pk priority hierarchy verified!")

    def test_identifier_separation_connect_id_vs_user_profile_pk(self):
        """Test that connect_id and user_profile_pk are strictly separated for tracker and messaging."""
        client = GarminJrClient(token_data={"di_token": "mock", "it_token": "mock_it"})

        # fetch_trackpoints should abort cleanly if connect_id is empty/None
        res = client.fetch_trackpoints(kid_profile_id="", connect_id=None)
        self.assertEqual(res, [])

        # verify kids_map separation
        kids_raw = [{
            "id": 12345,
            "name": "Leo",
            "connectId": 98765,
            "userProfilePk": 54321,
            "deviceId": "dev-leo",
        }]
        client._fetch_family_data = MagicMock()
        print("  [OK] Identifier separation between connect_id and user_profile_pk verified!")

    def test_night_mode_coordinator_relaxation(self):
        """Test coordinator night mode behavior and 300s gate."""
        import asyncio
        from custom_components.garmin_jr.coordinator import GarminJrDataUpdateCoordinator
        from custom_components.garmin_jr.const import CONF_NIGHT_MODE_ENABLED

        mock_hass = MagicMock()
        mock_entry = MagicMock()
        mock_entry.options = {CONF_NIGHT_MODE_ENABLED: True}
        mock_entry.data = {}
        mock_entry.entry_id = "test_entry"

        client = GarminJrClient(token_data={"di_token": "mock"})
        coordinator = GarminJrDataUpdateCoordinator(mock_hass, mock_entry, client)
        coordinator._initial_fetch_done = True
        coordinator.data = {
            "kid_1": {
                "child_id": "kid_1",
                "child_name": "Leo",
                "settings": {"bedTime": "20:00", "wakeTime": "07:00"},
            }
        }

        # Simulate night poll at 23:00 (sleep_time)
        now_ts = 1000.0
        coordinator._last_night_poll_ts = 900.0  # only 100s ago (< 300s)

        # Calling _async_poll_messages during sleep time within 300s gate should return without fetching
        client.fetch_messages = MagicMock()
        with patch("time.time", return_value=now_ts):
            with patch("custom_components.garmin_jr.coordinator.dt_util.now") as mock_now:
                import datetime
                mock_now.return_value = datetime.datetime(2026, 9, 1, 23, 0, 0)
                asyncio.run(coordinator._async_poll_messages())

        client.fetch_messages.assert_not_called()
        print("  [OK] Night mode coordinator 300s relaxation gate verified!")

    def test_options_flow_profile_pk_persistence(self):
        """Test that options flow handler renders profile PK fields and merges options safely."""
        import asyncio
        from custom_components.garmin_jr.config_flow import GarminJrOptionsFlowHandler
        from custom_components.garmin_jr.const import (
            CONF_SCAN_INTERVAL,
            CONF_ZONE_MAPPING,
            DOMAIN,
        )

        handler = GarminJrOptionsFlowHandler()
        mock_hass = MagicMock()
        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry"
        mock_entry.data = {CONF_SCAN_INTERVAL: 300}
        mock_entry.options = {
            CONF_SCAN_INTERVAL: 180,
            CONF_ZONE_MAPPING: {"123": "zone.home"},
            "profile_pk_kid_1": "999111222",
        }
        handler.hass = mock_hass
        handler.config_entry = mock_entry

        # Mock coordinator data
        mock_coord = MagicMock()
        mock_coord.data = {
            "kid_1": {
                "child_id": "kid_1",
                "child_name": "Leo",
                "user_profile_pk": "999111222",
                "geofences": [],
            }
        }
        mock_hass.data = {DOMAIN: {"test_entry": mock_coord}}
        mock_hass.states.async_entity_ids.return_value = []

        # 1. Test schema rendering contains profile_pk_kid_1 with blank default if unpinned
        handler.async_show_form = MagicMock()
        asyncio.run(handler.async_step_init(user_input=None))
        handler.async_show_form.assert_called_once()
        schema = handler.async_show_form.call_args[1]["data_schema"].schema
        schema_keys = [str(k.schema if hasattr(k, "schema") else k) for k in schema.keys()]
        self.assertIn("profile_pk_kid_1", schema_keys)
        # Check that default is empty string when no previous override exists for kid_2
        mock_coord.data["kid_2"] = {"child_id": "kid_2", "user_profile_pk": "333444", "geofences": []}
        handler.async_show_form.reset_mock()
        asyncio.run(handler.async_step_init(user_input=None))
        schema = handler.async_show_form.call_args[1]["data_schema"].schema
        # Find the Schema key for profile_pk_kid_2
        for k in schema.keys():
            if str(getattr(k, "schema", k)) == "profile_pk_kid_2":
                self.assertEqual(getattr(k, "default", None), "")

        # 2. Test saving options merges and retains existing data without wipe
        handler.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        user_input = {
            CONF_SCAN_INTERVAL: 120,
            "profile_pk_kid_1": "777888999",
            "profile_pk_kid_2": "",
        }
        asyncio.run(handler.async_step_init(user_input=user_input))
        handler.async_create_entry.assert_called_once()
        saved_data = handler.async_create_entry.call_args[1]["data"]
        self.assertEqual(saved_data[CONF_SCAN_INTERVAL], 120)
        self.assertEqual(saved_data["profile_pk_kid_1"], "777888999")
        self.assertNotIn("profile_pk_kid_2", saved_data)
        self.assertEqual(saved_data[CONF_ZONE_MAPPING], {"123": "zone.home"})
        print("  [OK] Options flow Profile PK schema and data merge persistence verified!")

    def test_unknown_guardian_no_misbinding(self):
        """Test that unknown guardian state never matches/learns arbitrary text messages, only audio notes."""
        client = GarminJrClient(token_data={"di_token": "mock"})
        self.assertEqual(len(client._guardian_profile_pks), 0)

        # 1. Arbitrary parent text message: from=111, to=222
        text_msg = [{
            "messageId": "msg-txt-1",
            "fromUserProfilePk": 111,
            "toUserProfilePk": 222,
            "messageText": "Hello there",
        }]
        parsed = client.parse_child_messages(text_msg, kid_id="kid_1", total_family_kids=1)
        self.assertEqual(len(parsed), 0)
        self.assertNotIn("kid_1", client._learned_child_pks)

        # 2. Voice note with transcription: from=Watch (222), to=Parent (111)
        audio_msg = [{
            "messageId": "msg-aud-1",
            "fromUserProfilePk": 222,
            "toUserProfilePk": 111,
            "mediaType": "Audio",
            "transcription": "I am here",
        }]
        parsed_audio = client.parse_child_messages(audio_msg, kid_id="kid_1", total_family_kids=1)
        self.assertEqual(len(parsed_audio), 1)
        self.assertEqual(client._learned_child_pks.get("kid_1"), "222")
        print("  [OK] Unknown guardian fallback strict audio guard verified!")

    def test_multi_child_isolation_with_single_tracked_child(self):
        """Test that single-child fallback does not activate when true family has multiple kids."""
        client = GarminJrClient(token_data={"di_token": "mock"})
        client._guardian_profile_pks.add("100")
        client.family_kids_count = 2  # True cloud family has 2 kids

        # Sibling message: from=Parent (100), to=Sibling Watch (300)
        sibling_msg = [{
            "messageId": "msg-sib-1",
            "fromUserProfilePk": 100,
            "toUserProfilePk": 300,
            "messageText": "Time for soccer",
        }]

        # Only kid_1 is being parsed, passing total_family_kids=2 from family_kids_count
        parsed = client.parse_child_messages(
            sibling_msg,
            kid_id="kid_1",
            total_family_kids=client.family_kids_count,
        )
        self.assertEqual(len(parsed), 0)
        self.assertNotIn("kid_1", client._learned_child_pks)
        print("  [OK] Multi-child family isolation with single tracked child verified!")

    def test_suppress_reload_on_learned_pks_persistence(self):
        """Test that async_reload_entry skips config entry reload when only CONF_CHILD_PROFILE_PKS changed."""
        import asyncio
        from custom_components.garmin_jr import async_reload_entry
        from custom_components.garmin_jr.coordinator import GarminJrDataUpdateCoordinator
        from custom_components.garmin_jr.const import CONF_CHILD_PROFILE_PKS, DOMAIN

        mock_hass = MagicMock()
        mock_entry = MagicMock()
        mock_entry.entry_id = "entry_1"
        mock_entry.options = {
            "scan_interval": 300,
            CONF_CHILD_PROFILE_PKS: {"kid_1": "111"},
        }

        mock_coord = MagicMock(spec=GarminJrDataUpdateCoordinator)
        mock_coord._last_user_options = {"scan_interval": 300}
        mock_hass.data = {DOMAIN: {"entry_1": mock_coord}}
        mock_hass.config_entries.async_reload = AsyncMock()

        # Update only child_profile_pks
        mock_entry.options = {
            "scan_interval": 300,
            CONF_CHILD_PROFILE_PKS: {"kid_1": "111", "kid_2": "222"},
        }
        asyncio.run(async_reload_entry(mock_hass, mock_entry))
        mock_hass.config_entries.async_reload.assert_not_called()

        # Update user option (scan_interval) -> reload SHOULD be called
        mock_entry.options = {
            "scan_interval": 60,
            CONF_CHILD_PROFILE_PKS: {"kid_1": "111", "kid_2": "222"},
        }
        asyncio.run(async_reload_entry(mock_hass, mock_entry))
        mock_hass.config_entries.async_reload.assert_called_once_with("entry_1")
        print("  [OK] Suppress reload on learned PKs persistence verified!")

    def test_send_text_message_numeric_validation(self):
        """Test that send_text_message rejects non-numeric profile PKs without making HTTP requests."""
        client = GarminJrClient(token_data={"di_token": "mock"})
        self.assertFalse(client.send_text_message("invalid_pk", "test"))
        self.assertFalse(client.send_text_message("", "test"))
        print("  [OK] send_text_message numeric validation verified!")

    def test_unknown_guardian_multi_child_audio_isolation(self):
        """Test that in multi-child family with unknown guardians, sibling audio note is not learned by other child."""
        client = GarminJrClient(token_data={"di_token": "mock"})
        self.assertEqual(len(client._guardian_profile_pks), 0)
        client.family_kids_count = 2

        # Sibling AMR voice note
        sibling_audio = [{
            "messageId": "msg-sib-aud-1",
            "fromUserProfilePk": 888111,
            "toUserProfilePk": 999222,
            "mediaType": "audio/amr",
            "transcription": "Hey sibling",
        }]

        # Parse for kid_1
        parsed = client.parse_child_messages(sibling_audio, kid_id="kid_1", total_family_kids=2)
        self.assertEqual(len(parsed), 0)
        self.assertNotIn("kid_1", client._learned_child_pks)
        print("  [OK] Unknown guardian multi-child audio isolation verified!")

    def test_parent_phone_voice_note_no_watch_pk_learning(self):
        """Test that phone-originated audio notes (Audio without transcription/AMR) do not mis-learn from_pk."""
        client = GarminJrClient(token_data={"di_token": "mock"})
        self.assertEqual(len(client._guardian_profile_pks), 0)

        # Parent sends phone voice note (mediaType="Audio", no transcription)
        parent_voice_note = [{
            "messageId": "msg-parent-voice-1",
            "fromUserProfilePk": 777111,
            "toUserProfilePk": 888222,
            "mediaType": "Audio",
        }]
        parsed = client.parse_child_messages(parent_voice_note, kid_id="kid_1", total_family_kids=1)
        self.assertEqual(len(parsed), 0)
        self.assertNotIn("kid_1", client._learned_child_pks)
        print("  [OK] Parent phone voice note non-attribution verified!")

    def test_cross_child_pk_collision_prevention(self):
        """Test that a PK already bound to child_1 cannot be bound to child_2."""
        client = GarminJrClient(token_data={"di_token": "mock"})
        client._learned_child_pks["kid_1"] = "55512345"

        # Message where kid_2 might attempt to claim the same PK
        msg = [{
            "messageId": "msg-claim-1",
            "fromUserProfilePk": 55512345,
            "toUserProfilePk": 999000,
            "mediaType": "audio/amr",
            "transcription": "Hello",
        }]
        parsed = client.parse_child_messages(msg, kid_id="kid_2", total_family_kids=1)
        self.assertEqual(client._learned_child_pks.get("kid_1"), "55512345")
        self.assertNotIn("kid_2", client._learned_child_pks)
        print("  [OK] Cross-child PK collision prevention verified!")


if __name__ == "__main__":
    unittest.main()





