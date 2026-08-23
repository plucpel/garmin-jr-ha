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
        }
        client = GarminJrClient(token_data=tokens)
        dumped = client.get_token_data()
        self.assertEqual(dumped["di_token"], "mock_access_token")
        self.assertEqual(dumped["di_refresh_token"], "mock_refresh_token")

    def test_fetch_all_data_vivokid_mocked(self):
        """Test parsing of Vivokid family and kids leaderboard data."""
        client = GarminJrClient(token_data={"di_token": "mock"})
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
                            "displayName": "Alex",
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

        data = client.fetch_all_data()
        self.assertIn("98765432", data)
        record = data["98765432"]

        self.assertEqual(record["child_name"], "Alex")
        self.assertEqual(record["model"], "Garmin Bounce")
        self.assertEqual(record["steps"], 6250)
        self.assertEqual(record["daily_step_goal"], 7500)
        self.assertEqual(record["steps_record"], 25000)
        self.assertEqual(record["active_minutes_record"], 180)
        self.assertEqual(record["family_name"], "Test Family")
        print("  [OK] Vivokid kids & family telemetry parsing verified!")

if __name__ == "__main__":
    unittest.main()
