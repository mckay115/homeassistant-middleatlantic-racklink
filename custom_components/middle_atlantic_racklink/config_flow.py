"""Config flow for Middle Atlantic RackLink integration."""

from __future__ import annotations

from .const import (
    CONF_MODEL,
    CONF_PDU_NAME,
    CONF_SCAN_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USERNAME,
    DOMAIN,
    MODEL_DESCRIPTIONS,
    SUPPORTED_MODELS,
)
from .controller.racklink_controller import RacklinkController
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback, HomeAssistant
from homeassistant.data_entry_flow import AbortFlow, FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import TextSelector
from typing import Any, Dict, Optional

import asyncio
import logging
import re
import voluptuous as vol

_LOGGER = logging.getLogger(__name__)
CONNECTION_TIMEOUT = 30  # Timeout in seconds for connection validation

# Data schema for the user input in the config flow
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): str,
    }
)

# Data schema for the options flow
OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
            vol.Coerce(int), vol.Range(min=5, max=300)
        ),
    }
)


async def validate_connection(
    hass: HomeAssistant, data: Dict[str, Any]
) -> Dict[str, Any]:
    """Validate the connection to the RackLink PDU."""
    host = data[CONF_HOST]
    port = data[CONF_PORT]
    username = data[CONF_USERNAME]
    password = data[CONF_PASSWORD]

    controller = RacklinkController(
        host=host,
        port=port,
        username=username,
        password=password,
        timeout=CONNECTION_TIMEOUT,
    )

    try:
        # Connect to the device
        if not await controller.connect():
            _LOGGER.error("Failed to connect to device: %s:%s", host, port)
            raise CannotConnect("Connection failed")

        # Try to retrieve device information
        await controller.update()

        # Validate that we got some basic information
        if not controller.pdu_name and not controller.pdu_model:
            _LOGGER.error("Failed to retrieve device information")
            await controller.disconnect()
            raise CannotConnect("Failed to retrieve device information")

        # Get device information for the config entry title
        info = {
            "pdu_name": controller.pdu_name or "RackLink PDU",
            "pdu_model": controller.pdu_model or "Unknown Model",
            "pdu_firmware": controller.pdu_firmware or "Unknown Firmware",
            "pdu_serial": controller.pdu_serial or "Unknown Serial",
            "mac_address": controller.mac_address or "Unknown MAC",
        }

        _LOGGER.info(
            "Successfully connected to %s (%s)", info["pdu_name"], info["pdu_model"]
        )

        # Properly disconnect from the device
        await controller.disconnect()
        return info

    except asyncio.TimeoutError:
        _LOGGER.error("Timeout connecting to device: %s:%s", host, port)
        await controller.disconnect()
        raise CannotConnect("Connection timeout")
    except (OSError, asyncio.exceptions.CancelledError) as err:
        _LOGGER.error("Error connecting to device: %s", err)
        await controller.disconnect()
        raise CannotConnect(f"Connection error: {err}")
    except ValueError as err:
        _LOGGER.error("Authentication failed: %s", err)
        await controller.disconnect()
        raise InvalidAuth(f"Authentication failed: {err}")
    except Exception as err:
        _LOGGER.error("Error connecting to device: %s", err)
        await controller.disconnect()
        raise CannotConnect(f"Error connecting to device: {err}")


class MiddleAtlanticRacklinkConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Middle Atlantic RackLink."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "OptionsFlowHandler":
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_connection(self.hass, user_input)

                # Set unique ID before checking for duplicates
                mac_address = info["mac_address"]
                if mac_address != "Unknown MAC":
                    await self.async_set_unique_id(mac_address)
                    try:
                        self._abort_if_unique_id_configured()
                    except AbortFlow as err:
                        return self.async_abort(reason=err.reason)

                # Create a friendly title for the config entry
                title = info["pdu_name"]
                if info["pdu_model"] != "Unknown Model":
                    title = f"{title} ({info['pdu_model']})"

                return self.async_create_entry(title=title, data=user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_import(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle import from configuration.yaml."""
        return await self.async_step_user(user_input)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Middle Atlantic RackLink."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle options flow."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
            }
        )

        return self.async_show_form(step_id="init", data_schema=data_schema)


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
