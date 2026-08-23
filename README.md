# Garmin Jr & Bounce Integration for Home Assistant

Custom Home Assistant component for Garmin Jr. and Garmin Bounce smartwatches. Provides GPS location tracking for map and zone-based presence detection, as well as step counts, step goals, personal records, battery levels, active minutes, and sync telemetry.

---

## Features

- **Sensors**:
  - `sensor.<child_name>_daily_steps`: Live daily step count.
  - `sensor.<child_name>_daily_step_goal`: Daily step goal.
  - `sensor.<child_name>_steps_record`: All-time daily step record.
  - `sensor.<child_name>_active_minutes_record`: All-time active minutes record.
  - `sensor.<child_name>_battery`: Battery level percentage and status.
  - `sensor.<child_name>_active_minutes`: Daily active minutes.
  - `sensor.<child_name>_last_sync`: Timestamp of the last device synchronization.
- **Device Tracker (`device_tracker.<child_name>_location`)**:
  - GPS coordinates and accuracy.
  - Automatic Home Assistant Zone mapping (`zone.home`, `zone.school`, etc.) for presence automations.
- **Dual Support**:
  - Discovers Garmin Jr family accounts and child watches (Bounce, vívofit jr.).
  - Discovers adult Garmin Connect watches registered to the parent account.

---

## Installation

### Option 1: HACS (Recommended)
1. In Home Assistant, open **HACS** > **Integrations**.
2. Click the three dots in the top right corner and select **Custom repositories**.
3. Add the repository URL: `https://github.com/plucpel/garmin-jr-ha` with category **Integration**.
4. Click **Download**, then restart Home Assistant.

### Option 2: Manual Installation
1. Copy the `custom_components/garmin_jr` directory into your Home Assistant `<config>/custom_components/` directory.
2. Restart Home Assistant.

---

## Configuration

1. In Home Assistant, go to **Settings** > **Devices & Services** > **Add Integration**.
2. Search for **Garmin Jr**.
3. Choose your authentication method:
   - **Login with Email and Password**: Enter your Garmin credentials. If MFA is enabled on your account, you will be prompted for the code.
   - **Import Saved Session Token**: Paste your saved `garmin_tokens.json` to authenticate.

---

## License
MIT License
