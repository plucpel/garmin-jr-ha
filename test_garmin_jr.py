"""Test script for Garmin Jr custom component."""
import json
import os
import py_compile
import sys
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

# Test GarminJrClient parsing with mock responses
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "custom_components", "garmin_jr"))
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

    def test_send_message_and_location_request(self):
        """Test send_text_message and request_location_update."""
        client = GarminJrClient(token_data={"di_token": "mock", "it_token": "mock_it"})
        
        with unittest.mock.patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"status": "SUCCESS"}

            sent = client.send_text_message(98765432, "Hello from Home Assistant!")
            self.assertTrue(sent)
            self.assertTrue(mock_post.called)

            loc_req = client.request_location_update(98765432)
            self.assertTrue(loc_req)
            print("  [OK] Message dispatch and location update request verified!")

if __name__ == "__main__":
    unittest.main()

