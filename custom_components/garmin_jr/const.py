"""Constants for the Garmin Jr integration."""
import logging

LOGGER = logging.getLogger(__package__)

DOMAIN = "garmin_jr"

# Configuration options
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_TOKEN_DATA = "token_data"
CONF_MFA_CODE = "mfa_code"
CONF_TOKENS = "tokens"
CONF_DI_TOKEN = "di_token"
CONF_DI_REFRESH_TOKEN = "di_refresh_token"
CONF_DI_CLIENT_ID = "di_client_id"

CONF_SCAN_INTERVAL = "scan_interval"
CONF_ZONE_MAPPING = "zone_mapping"
CONF_SCHOOL_MODE_ENABLED = "school_mode_enabled"
CONF_SCHOOL_MODE_START_TIME = "school_mode_start_time"
CONF_SCHOOL_MODE_END_TIME = "school_mode_end_time"
CONF_NIGHT_MODE_ENABLED = "night_mode_enabled"
CONF_CHILD_PROFILE_PKS = "child_profile_pks"
CONF_LLM_URL = "llm_url"
CONF_LLM_MODEL = "llm_model"
CONF_GARAGE_COVER_ENTITY = "garage_cover_entity"
CONF_HOME_SAFE_ZONE = "home_safe_zone"
CONF_CLEAR_LEARNED_PKS = "clear_learned_pks"

DEFAULT_SCAN_INTERVAL = 300  # 5 minutes
DEFAULT_SCHOOL_MODE_START_TIME = "08:00"
DEFAULT_SCHOOL_MODE_END_TIME = "15:00"
DEFAULT_LLM_URL = "http://127.0.0.1:13305"
DEFAULT_LLM_MODEL = "gemma4-it-e2b-FLM"
DEFAULT_GARAGE_COVER_ENTITY = "cover.garage_door"
DEFAULT_HOME_SAFE_ZONE = "Papa"

PLATFORMS = ["device_tracker", "sensor", "switch"]

# Events
EVENT_MESSAGE_RECEIVED = "garmin_jr_message_received"

# Services
SERVICE_SEND_MESSAGE = "send_message"
SERVICE_REQUEST_LOCATION_UPDATE = "request_location_update"
SERVICE_SPOT_PLANE = "spot_plane"
SERVICE_PROCESS_BOUNCE_MESSAGE = "process_bounce_message"

ATTR_MESSAGE = "message"
ATTR_MESSAGE_ID = "message_id"
ATTR_TEXT = "text"
ATTR_TARGET = "target"
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
ATTR_SCHOOL_MODE = "school_mode"
ATTR_IN_SCHOOL_MODE = "in_school_mode"
ATTR_OPERATING_MODE = "operating_mode"



