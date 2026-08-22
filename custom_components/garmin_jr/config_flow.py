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
    CONF_TOKENS,
    DEFAULT_SCAN_INTERVAL,
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
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL,
            self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )

        schema = vol.Schema(
            {
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
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
        )
