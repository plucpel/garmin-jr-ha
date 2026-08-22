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

CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 300  # 5 minutes
MIN_SCAN_INTERVAL = 60  # 1 minute

PLATFORMS = ["device_tracker", "sensor"]

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
