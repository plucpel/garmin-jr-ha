# Garmin Jr & Bounce Integration for Home Assistant

Custom Home Assistant component for Garmin Jr. and Garmin Bounce smartwatches. Provides live GPS location tracking for map and zone-based presence detection, two-way messaging between Home Assistant and Garmin Bounce watches, step counts, step goals, personal records, battery levels, active minutes, and sync telemetry.

---

## ✨ Features

- **Device Tracker (`device_tracker.<child_name>_location`)**:
  - Real-time GPS coordinates (`latitude`, `longitude`, `accuracy`).
  - **Safe Zone & Geofence Coupling**: Automatically aligns with Garmin Safe Zones (both Wi-Fi and LTE/cellular) to conserve watch battery and eliminate indoor GPS drift.
  - **Configurable Zone Matching**: Match each Garmin geofence to any Home Assistant zone (`zone.home`, `zone.school`, etc.) directly from the integration Options Flow.
- **2-Way Messaging**:
  - `sensor.<child_name>_last_message`: Displays the latest text message, sender, media type, and timestamp.
  - **`garmin_jr_message_received` Event**: Fired whenever a new message arrives from a child's watch, enabling instant Home Assistant automations or TTS announcements.
  - **`garmin_jr.send_message` Action/Service**: Send custom text messages directly to any child's Bounce watch from HA scripts, automations, or dashboards.
- **Sensors**:
  - `sensor.<child_name>_safe_zone`: Active Garmin Safe Zone (e.g. `Home`, `School`, or `Outside`).
  - `sensor.<child_name>_daily_steps`: Live daily step count.
  - `sensor.<child_name>_daily_step_goal`: Daily step goal.
  - `sensor.<child_name>_steps_record`: All-time daily step record.
  - `sensor.<child_name>_active_minutes_record`: All-time active minutes record.
  - `sensor.<child_name>_battery`: Battery level percentage and status.
  - `sensor.<child_name>_active_minutes`: Daily active minutes.
  - `sensor.<child_name>_last_sync`: Timestamp of the last device synchronization.
- **Services**:
  - `garmin_jr.send_message`: Sends a text message to a specified child's Bounce watch.
  - `garmin_jr.request_location_update`: Pings the child's watch over LTE to immediately refresh its GPS fix.
- **Dynamic Discovery**:
  - Automatically discovers all Garmin Jr family profiles, children, and adult watches without any manual ID configuration.

---

## 🚀 Services & Actions

### `garmin_jr.send_message`
Sends a text message to a child's Garmin Bounce watch.

```yaml
action: garmin_jr.send_message
data:
  target: "Child Name" # Optional: child name or profile ID. Omit to target first child.
  message: "Dinner is ready, please head home!"
```

### `garmin_jr.request_location_update`
Forces the watch to acquire and report an immediate GPS fix over LTE.

```yaml
action: garmin_jr.request_location_update
data:
  target: "Child Name"
```

---

## 🔔 Events

### `garmin_jr_message_received`
Fired whenever a new message arrives from a child's watch.

**Event Data:**
- `child_id`: ID of the child profile.
- `child_name`: Display name of the child.
- `message_id`: Unique message ID.
- `text`: Message text content.
- `sender`: Sender display name.
- `media_type`: `Text` or `Audio`.
- `timestamp`: Message creation timestamp.

**Automation Example:**
```yaml
alias: "Announce incoming Garmin Jr message"
trigger:
  - platform: event
    event_type: garmin_jr_message_received
action:
  - action: tts.speak
    target:
      entity_id: media_player.living_room_speaker
    data:
      message: "{{ trigger.event.data.child_name }} says: {{ trigger.event.data.text }}"
```

---

## 📦 Installation

### Option 1: HACS (Recommended)
1. In Home Assistant, open **HACS** > **Integrations**.
2. Click the three dots in the top right corner and select **Custom repositories**.
3. Add the repository URL: `https://github.com/plucpel/garmin-jr-ha` with category **Integration**.
4. Click **Download**, then restart Home Assistant.

### Option 2: Manual Installation
1. Copy the `custom_components/garmin_jr` directory into your Home Assistant `<config>/custom_components/` directory.
2. Restart Home Assistant.

---

## ⚙️ Configuration

1. In Home Assistant, go to **Settings** > **Devices & Services** > **Add Integration**.
2. Search for **Garmin Jr**.
3. Choose your authentication method:
   - **Login with Email and Password**: Enter your Garmin credentials. If MFA is enabled, enter the verification code when prompted.
   - **Import Saved Session Token**: Paste your saved `garmin_tokens.json` to authenticate.

---

## License
MIT License

