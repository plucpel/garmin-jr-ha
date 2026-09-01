"""Config flow for Garmin Jr integration."""
from __future__ import annotations

import json
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_DI_CLIENT_ID,
    CONF_DI_REFRESH_TOKEN,
    CONF_DI_TOKEN,
    CONF_EMAIL,
    CONF_MFA_CODE,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_SCHOOL_MODE_ENABLED,
    CONF_SCHOOL_MODE_END_TIME,
    CONF_SCHOOL_MODE_START_TIME,
    CONF_TOKENS,
    CONF_ZONE_MAPPING,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SCHOOL_MODE_END_TIME,
    DEFAULT_SCHOOL_MODE_START_TIME,
    DOMAIN,
)
from .garmin_client import GarminJrAuthError, GarminJrClient, GarminJrConnectionError

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL)
        ),
        vol.Required(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
        vol.Optional(CONF_TOKENS): selector.TextSelector(
            selector.TextSelectorConfig(multiline=True)
        ),
    }
)

STEP_MFA_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MFA_CODE): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
        ),
    }
)


class GarminJrConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Garmin Jr."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._email: str | None = None
        self._password: str | None = None
        self._client: GarminJrClient | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            raw_tokens = user_input.get(CONF_TOKENS)
            email = user_input.get(CONF_EMAIL, "").strip().lower()
            password = user_input.get(CONF_PASSWORD)

            await self.async_set_unique_id(email)
            self._abort_if_unique_id_configured()

            # Case 1: Pre-existing JSON session tokens provided
            if raw_tokens and raw_tokens.strip():
                try:
                    token_dict = json.loads(raw_tokens)
                    client = GarminJrClient(email=email, token_data=token_dict)
                    is_valid = await self.hass.async_add_executor_job(
                        client.validate_session
                    )
                    if not is_valid:
                        errors["base"] = "invalid_auth"
                    else:
                        tokens = client.get_token_data()
                        return self.async_create_entry(
                            title=f"Garmin Jr ({email})",
                            data={
                                CONF_EMAIL: email,
                                CONF_DI_TOKEN: tokens.get("di_token"),
                                CONF_DI_REFRESH_TOKEN: tokens.get("di_refresh_token"),
                                CONF_DI_CLIENT_ID: tokens.get("di_client_id"),
                                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                            },
                        )
                except json.JSONDecodeError:
                    errors["base"] = "invalid_tokens_json"
                except Exception as err:
                    _LOGGER.error("Error setting up from tokens: %s", err)
                    errors["base"] = "cannot_connect"

            # Case 2: Standard Username/Password login
            elif email and password:
                self._email = email
                self._password = password
                self._client = GarminJrClient(email=email, password=password)

                try:
                    status, _ = await self.hass.async_add_executor_job(
                        self._client.login_sync
                    )

                    if status == "needs_mfa":
                        return await self.async_step_mfa()

                    # Login succeeded directly
                    tokens = self._client.get_token_data()
                    return self.async_create_entry(
                        title=f"Garmin Jr ({email})",
                        data={
                            CONF_EMAIL: email,
                            CONF_DI_TOKEN: tokens.get("di_token"),
                            CONF_DI_REFRESH_TOKEN: tokens.get("di_refresh_token"),
                            CONF_DI_CLIENT_ID: tokens.get("di_client_id"),
                            CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                        },
                    )
                except GarminJrAuthError:
                    errors["base"] = "invalid_auth"
                except GarminJrConnectionError:
                    errors["base"] = "cannot_connect"
                except Exception as err:
                    _LOGGER.exception("Unexpected exception during login: %s", err)
                    errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_mfa(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle MFA verification code step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            mfa_code = user_input[CONF_MFA_CODE].strip()
            if self._client:
                try:
                    await self.hass.async_add_executor_job(
                        self._client.resume_mfa_sync, mfa_code
                    )

                    tokens = self._client.get_token_data()
                    return self.async_create_entry(
                        title=f"Garmin Jr ({self._email})",
                        data={
                            CONF_EMAIL: self._email,
                            CONF_DI_TOKEN: tokens.get("di_token"),
                            CONF_DI_REFRESH_TOKEN: tokens.get("di_refresh_token"),
                            CONF_DI_CLIENT_ID: tokens.get("di_client_id"),
                            CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                        },
                    )
                except GarminJrAuthError:
                    errors["base"] = "invalid_mfa"
                except Exception as err:
                    _LOGGER.error("Error resuming MFA: %s", err)
                    errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="mfa",
            data_schema=STEP_MFA_DATA_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> GarminJrOptionsFlowHandler:
        """Get the options flow handler."""
        return GarminJrOptionsFlowHandler()


class GarminJrOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Garmin Jr."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        # 1. Discover Garmin Safe Zones (geofences) from coordinator data
        coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id) if self.hass else None
        geofences: list[dict[str, Any]] = []
        if coordinator and coordinator.data:
            for child_id, child_data in coordinator.data.items():
                for gf in child_data.get("geofences", []):
                    if gf.get("id") and gf not in geofences:
                        geofences.append(gf)

        if user_input is not None:
            scan_interval = user_input.get(
                CONF_SCAN_INTERVAL,
                self.config_entry.options.get(
                    CONF_SCAN_INTERVAL,
                    self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ),
            )

            # Collect zone mapping selections
            zone_mapping: dict[str, str] = dict(self.config_entry.options.get(CONF_ZONE_MAPPING, {}))
            for key, val in user_input.items():
                if key == CONF_SCAN_INTERVAL or not val:
                    continue

                matched_id = None
                if "(" in key and ")" in key:
                    inside_parens = key.split("(")[1].split(")")[0].replace("ID:", "").strip()
                    if inside_parens.isdigit():
                        matched_id = inside_parens
                if not matched_id and key.startswith("zone_"):
                    suffix = key.replace("zone_", "").split("_")[0].strip()
                    if suffix.isdigit():
                        matched_id = suffix
                if not matched_id:
                    for gf in geofences:
                        gid = str(gf.get("id"))
                        if gid in key or key == gf.get("name"):
                            matched_id = gid
                            break

                if matched_id:
                    zone_mapping[matched_id] = str(val)

            return self.async_create_entry(
                title="",
                data={
                    CONF_SCAN_INTERVAL: scan_interval,
                    CONF_ZONE_MAPPING: zone_mapping,
                    CONF_SCHOOL_MODE_ENABLED: user_input.get(CONF_SCHOOL_MODE_ENABLED, True),
                    CONF_SCHOOL_MODE_START_TIME: user_input.get(CONF_SCHOOL_MODE_START_TIME, DEFAULT_SCHOOL_MODE_START_TIME),
                    CONF_SCHOOL_MODE_END_TIME: user_input.get(CONF_SCHOOL_MODE_END_TIME, DEFAULT_SCHOOL_MODE_END_TIME),
                },
            )

        current_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL,
            self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )

        saved_mapping = self.config_entry.options.get(CONF_ZONE_MAPPING, {})
        current_school_enabled = self.config_entry.options.get(
            CONF_SCHOOL_MODE_ENABLED,
            self.config_entry.data.get(CONF_SCHOOL_MODE_ENABLED, True),
        )
        current_school_start = self.config_entry.options.get(
            CONF_SCHOOL_MODE_START_TIME,
            self.config_entry.data.get(CONF_SCHOOL_MODE_START_TIME, DEFAULT_SCHOOL_MODE_START_TIME),
        )
        current_school_end = self.config_entry.options.get(
            CONF_SCHOOL_MODE_END_TIME,
            self.config_entry.data.get(CONF_SCHOOL_MODE_END_TIME, DEFAULT_SCHOOL_MODE_END_TIME),
        )

        # 2. Fetch available Home Assistant zones
        zone_options = [
            selector.SelectOptionDict(value="auto", label="Auto-Detect (Match by Name / Proximity)"),
            selector.SelectOptionDict(value="none", label="None (Use Raw GPS / Don't Match)"),
        ]

        if self.hass:
            zone_entities = self.hass.states.async_entity_ids("zone")
            for z_id in sorted(zone_entities):
                z_state = self.hass.states.get(z_id)
                friendly = z_state.attributes.get("friendly_name") if z_state else z_id
                zone_options.append(selector.SelectOptionDict(value=z_id, label=f"{friendly} ({z_id})"))

        schema_dict: dict[Any, Any] = {
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=current_interval,
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=60,
                    max=3600,
                    step=30,
                    unit_of_measurement="seconds",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_SCHOOL_MODE_ENABLED,
                default=current_school_enabled,
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_SCHOOL_MODE_START_TIME,
                default=current_school_start,
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
            vol.Required(
                CONF_SCHOOL_MODE_END_TIME,
                default=current_school_end,
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
        }

        # 3. Add dynamic selectors for each discovered Garmin Safe Zone with human-readable names
        for gf in geofences:
            gf_id = str(gf.get("id"))
            gf_name = gf.get("name") or f"Geofence {gf_id}"
            wifi_ssid = gf.get("wifi_ssid")
            wifi_str = f" [Wi-Fi: {wifi_ssid}]" if wifi_ssid else ""
            field_key = f"{gf_name} ({gf_id}){wifi_str}"
            default_val = saved_mapping.get(gf_id, "auto")

            schema_dict[vol.Optional(field_key, default=default_val)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=zone_options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
        )

