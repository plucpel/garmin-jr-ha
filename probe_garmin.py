#!/usr/bin/env python3
"""Garmin Jr & Bounce API Explorer Script."""
import json
import os
import sys
import time
from garminconnect.client import Client

TOKEN_PATH = os.path.expanduser("~/.garminconnect/garmin_tokens.json")

def main():
    if not os.path.exists(TOKEN_PATH):
        print(f"Token file not found at {TOKEN_PATH}. Please run python3 ~/garmin_token.py first.")
        sys.exit(1)

    with open(TOKEN_PATH, "r") as f:
        token_data = f.read()

    client = Client()
    try:
        client.loads(token_data)
        print("✅ Session token loaded successfully.")
    except Exception as e:
        print(f"❌ Failed to load session tokens: {e}")
        sys.exit(1)

    print("\n🔍 Validating session with user profile...")
    try:
        profile = client.connectapi("/userprofile-service/socialProfile")
        print(f"👤 Logged in as: {profile.get('fullName')} ({profile.get('userName')})")
        display_name = profile.get("displayName")
        user_guid = profile.get("garminGUID")
        user_id = str(profile.get("profileId") or profile.get("id") or "")
        print(f"🆔 Display Name: {display_name} | User GUID: {user_guid} | Profile ID: {user_id}")
    except Exception as e:
        print(f"❌ Session invalid / expired ({e}). Please regenerate token via python3 ~/garmin_token.py")
        sys.exit(1)

    print("\n=======================================================")
    print("🚀 PROBING GARMIN SERVICES & ENDPOINTS FOR BOUNCE 2 / JR")
    print("=======================================================")

    candidate_endpoints = [
        # Family & Child services
        "/family-service/family",
        f"/family-service/family/{user_id}",
        f"/family-service/family/user/{display_name}",
        f"/family-service/family/user/{user_guid}",
        "/family-service/family/members",
        "/family-service/family/children",
        "/family-service/family/user",
        "/family-service/family/summary",
        "/family-service/user/family",
        "/child-operations/family",
        f"/child-operations/family/{display_name}",
        "/child-operations/children",
        "/child-service/family",
        "/child-service/children",
        "/child-summary-service/family",
        "/child-summary/family",
        "/kids-service/family",
        "/kids-service/children",
        "/junior-service/family",
        "/vivofit-jr/family",
        "/parental-service/family",
        "/parental-service/children",
        "/safety-service/family",
        "/geofence-service/geofences",
        
        # User & Social Connections
        "/userprofile-service/socialProfile/connections",
        "/userprofile-service/userprofile/connections",
        "/userprofile-service/userprofile/family",
        "/userprofile-service/userprofile/relationships",
        
        # Device endpoints
        "/device-service/deviceregistration/devices",
        "/device-service/deviceregistration/devices/all",
        f"/device-service/device-info/user/{display_name}",
        f"/device-service/device-info/user/{user_id}",
        "/device-service/devicesummary/all",
        
        # LiveTrack & Location
        "/livetrack-service/livetrack/session",
        "/livetrack-service/livetrack/contacts",
        "/livetrack-service/livetrack/tokens",
        "/livetrack-service/livetrack/settings",
        
        # Wellness
        "/wellness-service/wellness/dailySummaryChart",
        f"/wellness-service/wellness/dailySummaryChart/{display_name}",
    ]

    headers = client.get_api_headers()
    sess = client._api_session

    results = {}
    for ep in candidate_endpoints:
        url = f"https://connectapi.garmin.com/{ep.lstrip('/')}"
        try:
            resp = sess.get(url, headers=headers, timeout=5)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    results[ep] = data
                    print(f"🎯 [200 OK] {ep}")
                    print(f"   Sample JSON: {json.dumps(data, indent=2)[:300]}...\n")
                except Exception:
                    print(f"🎯 [200 OK (text)] {ep} -> {resp.text[:100]}")
            elif resp.status_code in (401, 403):
                print(f"🔒 [{resp.status_code} FORBIDDEN] {ep}")
            elif resp.status_code != 404:
                print(f"⚠️ [{resp.status_code}] {ep}")
        except Exception as e:
            print(f"💥 [ERROR] {ep}: {e}")

    # Check other Garmin service domains
    print("\n--- Testing alternative Garmin service base URLs ---")
    other_bases = [
        "https://services.garmin.com",
        "https://mobile.garmin.com",
        "https://livetrack.garmin.com",
        "https://api.garmin.com",
    ]
    test_paths = [
        "/family-service/family",
        "/child-operations/family",
        "/child-service/family",
        "/device-service/deviceregistration/devices",
        "/livetrack-service/livetrack/session",
    ]
    for b in other_bases:
        for p in test_paths:
            full_url = f"{b}{p}"
            try:
                resp = sess.get(full_url, headers=headers, timeout=5)
                if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("application/json"):
                    data = resp.json()
                    results[full_url] = data
                    print(f"🎯 [200 OK JSON] {full_url}")
                    print(f"   Data: {json.dumps(data, indent=2)[:300]}...\n")
                elif resp.status_code in (401, 403):
                    print(f"🔒 [{resp.status_code}] {full_url}")
            except Exception:
                pass

    out_file = "/Users/ppelleti/ha_garmin_jr/probe_results.json"
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n📁 Full probe results written to: {out_file}")

if __name__ == "__main__":
    main()
