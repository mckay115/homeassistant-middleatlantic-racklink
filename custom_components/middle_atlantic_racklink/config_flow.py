"""Config flow for Middle Atlantic RackLink integration."""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any, Dict, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import TextSelector

from .const import (
    CONF_MODEL,
    CONF_PDU_NAME,
    DEFAULT_PORT,
    DOMAIN,
    MODEL_DESCRIPTIONS,
    SUPPORTED_MODELS,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
)
from .controller.racklink_controller import RacklinkController

_LOGGER = logging.getLogger(__name__)
CONNECTION_TIMEOUT = 15  # Timeout in seconds for connection validation

DEFAULT_USERNAME = "admin"


async def validate_connection(
    hass: HomeAssistant, data: Dict[str, Any]
) -> Dict[str, Any]:
    """Validate the connection by attempting to connect to the device."""
    host = data[CONF_HOST]
    port = data.get(CONF_PORT, DEFAULT_PORT)
    username = data.get(CONF_USERNAME, DEFAULT_USERNAME)
    password = data[CONF_PASSWORD]

    controller = RacklinkController(
        host=host, port=port, username=username, password=password
    )

    try:
        if not await controller.connect():
            raise CannotConnect("Failed to connect to device")

        # Try to get device info
        await controller.update()

        if not controller.pdu_serial:
            raise InvalidAuth("Failed to retrieve device information")

        # Return device info for title
        return {
            "title": controller.pdu_name or f"RackLink PDU ({host})",
            "serial": controller.pdu_serial,
        }
    except Exception as err:
        _LOGGER.error("Error connecting to device: %s", err)
        raise CannotConnect(f"Connection error: {err}")
    finally:
        await controller.disconnect()


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Middle Atlantic RackLink."""

    VERSION = 1

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            try:
                device_info = await validate_connection(self.hass, user_input)

                # Check if already configured
                await self.async_set_unique_id(device_info["serial"])
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=device_info["title"],
                    data=user_input,
                )
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        # Show form
        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    async def async_step_import(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle import from configuration.yaml."""
        return await self.async_step_user(user_input)

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "OptionsFlowHandler":
        """Return the options flow."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Middle Atlantic RackLink."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = {
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=self.config_entry.options.get(
                    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                ),
            ): int,
        }

        return self.async_show_form(step_id="init", data_schema=vol.Schema(options))


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
