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

    def test_fetch_all_data_mocked(self):
        """Test parsing of device list and location data."""
        client = GarminJrClient(token_data={"di_token": "mock"})
        client.client.di_token = "mock"
        client.client._token_expires_soon = MagicMock(return_value=False)

        mock_devices = [
            {
                "deviceId": "123456789",
                "displayName": "Leo's Bounce",
                "productDisplayName": "Garmin Bounce",
                "partNumber": "010-02448-00",
                "batteryLevel": 88,
                "batteryStatus": "NORMAL",
                "stepGoal": 7000,
                "lastSyncTime": "2026-08-22T10:00:00Z",
            }
        ]

        def mock_connectapi(endpoint):
            if "deviceregistration/devices" in endpoint:
                return mock_devices
            if "location" in endpoint:
                return {
                    "latitude": 45.5017,
                    "longitude": -73.5673,
                    "horizontalAccuracy": 12,
                    "timestamp": "2026-08-22T11:45:00Z",
                }
            if "usersummary/daily" in endpoint:
                return {
                    "totalSteps": 5420,
                    "activeMinutes": 45,
                }
            return {}

        client.client.connectapi = MagicMock(side_effect=mock_connectapi)

        data = client.fetch_all_data()
        self.assertIn("123456789", data)
        record = data["123456789"]

        self.assertEqual(record["child_name"], "Leo's Bounce")
        self.assertEqual(record["battery_level"], 88)
        self.assertEqual(record["steps"], 5420)
        self.assertEqual(record["active_minutes"], 45)
        self.assertEqual(record["latitude"], 45.5017)
        self.assertEqual(record["longitude"], -73.5673)
        self.assertEqual(record["gps_accuracy"], 12)
        print("  [OK] Mock telemetry & location parsing verified!")

if __name__ == "__main__":
    unittest.main()
