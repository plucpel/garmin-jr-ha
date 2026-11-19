"""Config flow for Garmin Jr integration."""
from __future__ import annotations

from collections.abc import Mapping
import json
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_CHILD_PROFILE_PKS,
    CONF_CLEAR_LEARNED_PKS,
    CONF_DI_CLIENT_ID,
    CONF_DI_REFRESH_TOKEN,
    CONF_DI_TOKEN,
    CONF_EMAIL,
    CONF_GARAGE_COVER_ENTITY,
    CONF_HOME_SAFE_ZONE,
    CONF_LLM_MODEL,
    CONF_LLM_URL,
    CONF_MFA_CODE,
    CONF_NIGHT_MODE_ENABLED,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_SCHOOL_MODE_ENABLED,
    CONF_SCHOOL_MODE_END_TIME,
    CONF_SCHOOL_MODE_START_TIME,
    CONF_SCHOOL_SAFE_ZONE,
    CONF_TOKEN_DATA,
    CONF_TOKENS,
    CONF_ZONE_MAPPING,
    DEFAULT_GARAGE_COVER_ENTITY,
    DEFAULT_HOME_SAFE_ZONE,
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_URL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SCHOOL_MODE_END_TIME,
    DEFAULT_SCHOOL_MODE_START_TIME,
    DEFAULT_SCHOOL_SAFE_ZONE,
    DOMAIN,
)
from .garmin_client import GarminJrAuthError, GarminJrClient, GarminJrConnectionError

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL)
        ),
        vol.Optional(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
        vol.Optional(CONF_TOKENS): selector.TextSelector(
            selector.TextSelectorConfig(multiline=True)
        ),
    }
)

STEP_REAUTH_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_PASSWORD): selector.TextSelector(
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


def _build_entry_auth_data(email: str | None, tokens: dict[str, Any], scan_interval: int = DEFAULT_SCAN_INTERVAL) -> dict[str, Any]:
    """Build config entry data payload containing both full token bundle and legacy DI keys."""
    return {
        CONF_EMAIL: email,
        CONF_TOKEN_DATA: tokens,
        CONF_DI_TOKEN: tokens.get("di_token"),
        CONF_DI_REFRESH_TOKEN: tokens.get("di_refresh_token"),
        CONF_DI_CLIENT_ID: tokens.get("di_client_id"),
        CONF_SCAN_INTERVAL: scan_interval,
    }


class GarminJrConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Garmin Jr."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._email: str | None = None
        self._password: str | None = None
        self._client: GarminJrClient | None = None
        self._reauth_entry: config_entries.ConfigEntry | None = None

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
                            data=_build_entry_auth_data(email, tokens),
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
                        data=_build_entry_auth_data(email, tokens),
                    )
                except GarminJrAuthError:
                    errors["base"] = "invalid_auth"
                except GarminJrConnectionError:
                    errors["base"] = "cannot_connect"
                except Exception as err:
                    _LOGGER.exception("Unexpected exception during login: %s", err)
                    errors["base"] = "unknown"
            else:
                errors["base"] = "invalid_auth"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> FlowResult:
        """Handle reauthentication when Garmin session tokens expire."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        self._email = str(entry_data.get(CONF_EMAIL) or "").strip().lower()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm reauthentication credentials or pasted tokens."""
        errors: dict[str, str] = {}
        email = self._email or ""

        if user_input is not None:
            raw_tokens = user_input.get(CONF_TOKENS)
            password = user_input.get(CONF_PASSWORD)

            if raw_tokens and raw_tokens.strip():
                try:
                    token_dict = json.loads(raw_tokens)
                    client = GarminJrClient(email=email, token_data=token_dict)
                    is_valid = await self.hass.async_add_executor_job(client.validate_session)
                    if not is_valid:
                        errors["base"] = "invalid_auth"
                    elif self._reauth_entry is not None:
                        tokens = client.get_token_data()
                        interval = self._reauth_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                        self.hass.config_entries.async_update_entry(
                            self._reauth_entry,
                            data={**self._reauth_entry.data, **_build_entry_auth_data(email, tokens, interval)},
                        )
                        await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                        return self.async_abort(reason="reauth_successful")
                except json.JSONDecodeError:
                    errors["base"] = "invalid_tokens_json"
                except Exception as err:
                    _LOGGER.error("Error reauthenticating from tokens: %s", err)
                    errors["base"] = "cannot_connect"
            elif email and password:
                self._password = password
                self._client = GarminJrClient(email=email, password=password)
                try:
                    status, _ = await self.hass.async_add_executor_job(self._client.login_sync)
                    if status == "needs_mfa":
                        return await self.async_step_mfa()
                    if self._reauth_entry is not None:
                        tokens = self._client.get_token_data()
                        interval = self._reauth_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                        self.hass.config_entries.async_update_entry(
                            self._reauth_entry,
                            data={**self._reauth_entry.data, **_build_entry_auth_data(email, tokens, interval)},
                        )
                        await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                        return self.async_abort(reason="reauth_successful")
                except GarminJrAuthError:
                    errors["base"] = "invalid_auth"
                except GarminJrConnectionError:
                    errors["base"] = "cannot_connect"
                except Exception as err:
                    _LOGGER.exception("Unexpected exception during reauth: %s", err)
                    errors["base"] = "unknown"
            else:
                errors["base"] = "invalid_auth"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_DATA_SCHEMA,
            description_placeholders={"email": email},
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
                    if self._reauth_entry is not None:
                        interval = self._reauth_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                        self.hass.config_entries.async_update_entry(
                            self._reauth_entry,
                            data={**self._reauth_entry.data, **_build_entry_auth_data(self._email, tokens, interval)},
                        )
                        await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                        return self.async_abort(reason="reauth_successful")

                    return self.async_create_entry(
                        title=f"Garmin Jr ({self._email})",
                        data=_build_entry_auth_data(self._email, tokens),
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
        gf_field_to_id: dict[str, str] = {}
        if coordinator and coordinator.data:
            for child_id, child_data in coordinator.data.items():
                for gf in child_data.get("geofences", []):
                    if gf.get("id") and gf not in geofences:
                        geofences.append(gf)

        for gf in geofences:
            gf_id = str(gf.get("id"))
            gf_name = gf.get("name") or f"Geofence {gf_id}"
            wifi_ssid = gf.get("wifi_ssid")
            wifi_str = f" [Wi-Fi: {wifi_ssid}]" if wifi_ssid else ""
            gf_field_to_id[f"{gf_name} ({gf_id}){wifi_str}"] = gf_id

        if user_input is not None:
            scan_interval = user_input.get(
                CONF_SCAN_INTERVAL,
                self.config_entry.options.get(
                    CONF_SCAN_INTERVAL,
                    self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ),
            )

            # Collect zone mapping selections using exact field lookup or explicit zone prefixes only
            zone_mapping: dict[str, str] = dict(self.config_entry.options.get(CONF_ZONE_MAPPING, {}))
            gf_names_to_id = {str(gf.get("name")): str(gf.get("id")) for gf in geofences if gf.get("name") and gf.get("id")}
            for key, val in user_input.items():
                if not val or key in (
                    CONF_SCAN_INTERVAL,
                    CONF_SCHOOL_MODE_ENABLED,
                    CONF_SCHOOL_MODE_START_TIME,
                    CONF_SCHOOL_MODE_END_TIME,
                    CONF_NIGHT_MODE_ENABLED,
                    CONF_LLM_URL,
                    CONF_LLM_MODEL,
                    CONF_GARAGE_COVER_ENTITY,
                    CONF_HOME_SAFE_ZONE,
                    CONF_SCHOOL_SAFE_ZONE,
                    CONF_CLEAR_LEARNED_PKS,
                ) or key.startswith("profile_pk_"):
                    continue

                matched_id = gf_field_to_id.get(key) or gf_names_to_id.get(key)
                if not matched_id and "(" in key and ")" in key:
                    inside_parens = key.split("(")[1].split(")")[0].replace("ID:", "").strip()
                    if inside_parens.isdigit():
                        matched_id = inside_parens
                if not matched_id and key.startswith("zone_"):
                    suffix = key.replace("zone_", "").split("_")[0].strip()
                    if suffix.isdigit():
                        matched_id = suffix

                if matched_id:
                    zone_mapping[matched_id] = str(val)

            existing_cover = self.config_entry.options.get(CONF_GARAGE_COVER_ENTITY, DEFAULT_GARAGE_COVER_ENTITY)
            existing_zone = self.config_entry.options.get(CONF_HOME_SAFE_ZONE, DEFAULT_HOME_SAFE_ZONE)
            existing_school_zone = self.config_entry.options.get(CONF_SCHOOL_SAFE_ZONE, DEFAULT_SCHOOL_SAFE_ZONE)

            # Start with existing options to ensure nothing is wiped
            entry_data = dict(self.config_entry.options)
            entry_data.pop(CONF_CLEAR_LEARNED_PKS, None)
            entry_data.update({
                CONF_SCAN_INTERVAL: scan_interval,
                CONF_ZONE_MAPPING: zone_mapping,
                CONF_SCHOOL_MODE_ENABLED: user_input.get(CONF_SCHOOL_MODE_ENABLED, False),
                CONF_SCHOOL_MODE_START_TIME: user_input.get(CONF_SCHOOL_MODE_START_TIME, DEFAULT_SCHOOL_MODE_START_TIME),
                CONF_SCHOOL_MODE_END_TIME: user_input.get(CONF_SCHOOL_MODE_END_TIME, DEFAULT_SCHOOL_MODE_END_TIME),
                CONF_NIGHT_MODE_ENABLED: user_input.get(CONF_NIGHT_MODE_ENABLED, False),
                CONF_LLM_URL: str(user_input.get(CONF_LLM_URL, DEFAULT_LLM_URL)).strip(),
                CONF_LLM_MODEL: str(user_input.get(CONF_LLM_MODEL, DEFAULT_LLM_MODEL)).strip(),
                CONF_GARAGE_COVER_ENTITY: str(user_input.get(CONF_GARAGE_COVER_ENTITY, existing_cover)).strip() or DEFAULT_GARAGE_COVER_ENTITY,
                CONF_HOME_SAFE_ZONE: str(user_input.get(CONF_HOME_SAFE_ZONE, existing_zone)).strip() or DEFAULT_HOME_SAFE_ZONE,
                CONF_SCHOOL_SAFE_ZONE: str(user_input.get(CONF_SCHOOL_SAFE_ZONE, existing_school_zone)).strip() or DEFAULT_SCHOOL_SAFE_ZONE,
            })

            if user_input.get(CONF_CLEAR_LEARNED_PKS):
                entry_data[CONF_CHILD_PROFILE_PKS] = {}
                if coordinator and hasattr(coordinator, "client") and hasattr(coordinator.client, "_learned_child_pks"):
                    coordinator.client._learned_child_pks.clear()

            # Save or remove per-child profile PK overrides
            for key, val in user_input.items():
                if key.startswith("profile_pk_"):
                    if val and str(val).strip():
                        entry_data[key] = str(val).strip()
                    elif key in entry_data:
                        del entry_data[key]

            return self.async_create_entry(
                title="",
                data=entry_data,
            )

        current_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL,
            self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )

        saved_mapping = self.config_entry.options.get(CONF_ZONE_MAPPING, {})
        current_school_enabled = self.config_entry.options.get(
            CONF_SCHOOL_MODE_ENABLED,
            self.config_entry.data.get(CONF_SCHOOL_MODE_ENABLED, False),
        )
        current_school_start = self.config_entry.options.get(
            CONF_SCHOOL_MODE_START_TIME,
            self.config_entry.data.get(CONF_SCHOOL_MODE_START_TIME, DEFAULT_SCHOOL_MODE_START_TIME),
        )
        current_school_end = self.config_entry.options.get(
            CONF_SCHOOL_MODE_END_TIME,
            self.config_entry.data.get(CONF_SCHOOL_MODE_END_TIME, DEFAULT_SCHOOL_MODE_END_TIME),
        )
        current_night_enabled = self.config_entry.options.get(
            CONF_NIGHT_MODE_ENABLED,
            self.config_entry.data.get(CONF_NIGHT_MODE_ENABLED, False),
        )
        current_llm_url = str(self.config_entry.options.get(
            CONF_LLM_URL,
            self.config_entry.data.get(CONF_LLM_URL, DEFAULT_LLM_URL),
        ))
        current_llm_model = str(self.config_entry.options.get(
            CONF_LLM_MODEL,
            self.config_entry.data.get(CONF_LLM_MODEL, DEFAULT_LLM_MODEL),
        ))
        current_garage_cover = str(self.config_entry.options.get(
            CONF_GARAGE_COVER_ENTITY,
            DEFAULT_GARAGE_COVER_ENTITY,
        ))
        current_home_zone = str(self.config_entry.options.get(
            CONF_HOME_SAFE_ZONE,
            DEFAULT_HOME_SAFE_ZONE,
        ))
        current_school_zone = str(self.config_entry.options.get(
            CONF_SCHOOL_SAFE_ZONE,
            DEFAULT_SCHOOL_SAFE_ZONE,
        ))

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
            vol.Required(
                CONF_NIGHT_MODE_ENABLED,
                default=current_night_enabled,
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_LLM_URL,
                default=current_llm_url,
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
            vol.Optional(
                CONF_LLM_MODEL,
                default=current_llm_model,
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
            vol.Optional(
                CONF_GARAGE_COVER_ENTITY,
                default=current_garage_cover,
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="cover")
            ),
            vol.Optional(
                CONF_HOME_SAFE_ZONE,
                default=current_home_zone,
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
            vol.Optional(
                CONF_SCHOOL_SAFE_ZONE,
                default=current_school_zone,
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
            vol.Optional(
                CONF_CLEAR_LEARNED_PKS,
                default=False,
            ): selector.BooleanSelector(),
        }

        # 3. Add per-child Profile PK override selectors (leave blank by default so auto-discovery remains active)
        if coordinator and coordinator.data:
            for child_id, child_data in coordinator.data.items():
                saved_override = str(self.config_entry.options.get(f"profile_pk_{child_id}", ""))
                field_key = f"profile_pk_{child_id}"
                schema_dict[vol.Optional(field_key, default=saved_override)] = selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                )

        # 4. Add dynamic selectors for each discovered Garmin Safe Zone with human-readable names
        for field_key, gf_id in gf_field_to_id.items():
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

