"""Config flow for Garmin Jr integration."""
from __future__ import annotations

import json
import os
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv

from .const import (
    AUTH_TYPE_CREDENTIALS,
    AUTH_TYPE_TOKEN,
    CONF_AUTH_TYPE,
    CONF_EMAIL,
    CONF_MFA_CODE,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN_DATA,
    CONF_TOKEN_PATH,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    MIN_SCAN_INTERVAL,
)
from .garmin_client import GarminJrAuthError, GarminJrClient, GarminJrConnectionError


class GarminJrConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Garmin Jr."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow."""
        self._auth_type: str = AUTH_TYPE_CREDENTIALS
        self._email: str | None = None
        self._password: str | None = None
        self._client: GarminJrClient | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - choose auth method."""
        if user_input is not None:
            self._auth_type = user_input[CONF_AUTH_TYPE]
            if self._auth_type == AUTH_TYPE_TOKEN:
                return await self.async_step_token()
            return await self.async_step_credentials()

        return self.async_show_menu(
            step_id="user",
            menu_options=["credentials", "token"],
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle login with email and password."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._email = user_input[CONF_EMAIL]
            self._password = user_input[CONF_PASSWORD]
            self._client = GarminJrClient(email=self._email, password=self._password)

            try:
                status, _ = await self.hass.async_add_executor_job(
                    self._client.login_sync
                )
                if status == "needs_mfa":
                    return await self.async_step_mfa()

                token_data = self._client.get_token_data()
                await self.async_set_unique_id(self._email.lower())
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"Garmin Jr ({self._email})",
                    data={
                        CONF_AUTH_TYPE: AUTH_TYPE_CREDENTIALS,
                        CONF_EMAIL: self._email,
                        CONF_TOKEN_DATA: token_data,
                    },
                )
            except GarminJrAuthError:
                errors["base"] = "invalid_auth"
            except GarminJrConnectionError:
                errors["base"] = "cannot_connect"
            except Exception as err:
                LOGGER.exception("Unexpected exception in credentials step: %s", err)
                errors["base"] = "unknown"

        schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL, default=self._email or ""): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
            }
        )
        return self.async_show_form(
            step_id="credentials",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_mfa(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle MFA code input."""
        errors: dict[str, str] = {}

        if user_input is not None and self._client is not None:
            mfa_code = user_input[CONF_MFA_CODE]
            try:
                await self.hass.async_add_executor_job(
                    self._client.resume_mfa_sync, mfa_code
                )
                token_data = self._client.get_token_data()
                await self.async_set_unique_id((self._email or "garmin_user").lower())
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"Garmin Jr ({self._email})",
                    data={
                        CONF_AUTH_TYPE: AUTH_TYPE_CREDENTIALS,
                        CONF_EMAIL: self._email,
                        CONF_TOKEN_DATA: token_data,
                    },
                )
            except GarminJrAuthError:
                errors["base"] = "invalid_mfa"
            except Exception as err:
                LOGGER.exception("Unexpected exception in MFA step: %s", err)
                errors["base"] = "unknown"

        schema = vol.Schema(
            {
                vol.Required(CONF_MFA_CODE): cv.string,
            }
        )
        return self.async_show_form(
            step_id="mfa",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_token(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle token file or raw token string import."""
        errors: dict[str, str] = {}

        if user_input is not None:
            token_path = user_input.get(CONF_TOKEN_PATH)
            raw_token = user_input.get(CONF_TOKEN_DATA)

            token_data_dict: dict[str, Any] | None = None
            if token_path and os.path.exists(os.path.expanduser(token_path)):
                try:
                    with open(os.path.expanduser(token_path), "r", encoding="utf-8") as f:
                        token_data_dict = json.load(f)
                except Exception as err:
                    errors["base"] = "invalid_token_file"

            elif raw_token:
                try:
                    token_data_dict = json.loads(raw_token)
                except Exception:
                    errors["base"] = "invalid_token_json"

            if token_data_dict and not errors:
                client = GarminJrClient(token_data=token_data_dict)
                valid = await self.hass.async_add_executor_job(client.validate_session)
                if not valid:
                    errors["base"] = "token_expired"
                else:
                    unique_id = token_data_dict.get("di_client_id") or "garmin_token_user"
                    await self.async_set_unique_id(unique_id)
                    self._abort_if_unique_id_configured()

                    return self.async_create_entry(
                        title="Garmin Jr (Token Session)",
                        data={
                            CONF_AUTH_TYPE: AUTH_TYPE_TOKEN,
                            CONF_TOKEN_DATA: token_data_dict,
                        },
                    )

        default_token_path = os.path.expanduser("~/.garminconnect/garmin_tokens.json")
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_TOKEN_PATH,
                    default=default_token_path if os.path.exists(default_token_path) else "",
                ): cv.string,
                vol.Optional(CONF_TOKEN_DATA): cv.string,
            }
        )
        return self.async_show_form(
            step_id="token",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> GarminJrOptionsFlowHandler:
        """Get the options flow handler."""
        return GarminJrOptionsFlowHandler(config_entry)


class GarminJrOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Garmin Jr."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

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
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=3600)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
