"""Constants for the Garmin Jr integration."""
from datetime import timedelta
import logging

LOGGER = logging.getLogger(__package__)

DOMAIN = "garmin_jr"

# Configuration options
CONF_AUTH_TYPE = "auth_type"
AUTH_TYPE_CREDENTIALS = "credentials"
AUTH_TYPE_TOKEN = "token"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_TOKEN_PATH = "token_path"
CONF_TOKEN_DATA = "token_data"
CONF_MFA_CODE = "mfa_code"
CONF_TOKENS = "tokens"
CONF_DI_TOKEN = "di_token"
CONF_DI_REFRESH_TOKEN = "di_refresh_token"
CONF_DI_CLIENT_ID = "di_client_id"
CONF_IT_TOKEN = "it_token"
CONF_IT_REFRESH_TOKEN = "it_refresh_token"
CONF_IT_EXPIRES_AT = "it_expires_at"

CONF_SCAN_INTERVAL = "scan_interval"
CONF_ZONE_MAPPING = "zone_mapping"
DEFAULT_SCAN_INTERVAL = 300  # 5 minutes
MIN_SCAN_INTERVAL = 60  # 1 minute

PLATFORMS = ["device_tracker", "sensor"]

# Events
EVENT_MESSAGE_RECEIVED = "garmin_jr_message_received"

# Services
SERVICE_SEND_MESSAGE = "send_message"
SERVICE_REQUEST_LOCATION_UPDATE = "request_location_update"
SERVICE_SET_STEP_GOAL = "set_step_goal"
SERVICE_SPOT_PLANE = "spot_plane"

ATTR_MESSAGE = "message"
ATTR_TARGET = "target"
ATTR_GOAL = "goal"
ATTR_SEND_TO_WATCH = "send_to_watch"
ATTR_LANGUAGE = "language"

# Data keys
ATTR_CHILD_ID = "child_id"
ATTR_CHILD_NAME = "child_name"
ATTR_DEVICE_ID = "device_id"
ATTR_DEVICE_NAME = "device_name"
ATTR_MODEL = "model"
ATTR_BATTERY_LEVEL = "battery_level"
ATTR_BATTERY_STATUS = "battery_status"
ATTR_STEPS = "steps"
ATTR_DAILY_STEP_GOAL = "daily_step_goal"
ATTR_ACTIVE_MINUTES = "active_minutes"
ATTR_LAST_SYNC = "last_sync"
ATTR_LATITUDE = "latitude"
ATTR_LONGITUDE = "longitude"
ATTR_ACCURACY = "gps_accuracy"
ATTR_LOCATION_TIMESTAMP = "location_timestamp"
ATTR_GEOFENCE_STATUS = "geofence_status"
ATTR_STEPS_RECORD = "steps_record"
ATTR_ACTIVE_MINUTES_RECORD = "active_minutes_record"
ATTR_FAMILY_ID = "family_id"
ATTR_FAMILY_NAME = "family_name"
ATTR_LAST_MESSAGE = "last_message"
ATTR_LAST_MESSAGE_TIME = "last_message_time"
ATTR_LAST_MESSAGE_SENDER = "last_message_sender"
ATTR_LAST_MESSAGE_MEDIA = "last_message_media"
ATTR_GARMIN_SAFE_ZONE = "garmin_safe_zone"
ATTR_GARMIN_GEOFENCE_ID = "garmin_geofence_id"
ATTR_MATCHED_HA_ZONE = "matched_ha_zone"
ATTR_FIX_TYPE = "fix_type"
ATTR_HAS_WIFI = "has_wifi"
ATTR_GEOFENCES = "geofences"



