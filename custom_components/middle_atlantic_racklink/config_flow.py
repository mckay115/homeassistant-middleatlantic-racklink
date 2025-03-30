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

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_USERNAME): str,
        vol.Optional(CONF_PASSWORD): str,
        vol.Optional(CONF_PDU_NAME): str,
    }
)


async def validate_input(hass: HomeAssistant, data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the user input allows us to connect."""
    host = data[CONF_HOST]
    port = data.get(CONF_PORT, DEFAULT_PORT)
    username = data.get(CONF_USERNAME)
    password = data.get(CONF_PASSWORD)
    pdu_name = data.get(CONF_PDU_NAME)

    controller = RacklinkController(
        host=host,
        port=port,
        username=username,
        password=password,
        pdu_name=pdu_name,
    )

    try:
        # Try to connect to the device
        if not await controller.connect():
            raise CannotConnect("Failed to connect to the PDU")

        # Get device information
        info = {
            "pdu_name": controller.pdu_name,
            "pdu_model": controller.pdu_model,
            "pdu_serial": controller.pdu_serial,
            "pdu_firmware": controller.pdu_firmware,
            "mac_address": controller.mac_address,
        }

        # Disconnect before returning
        await controller.disconnect()

        return info

    except asyncio.TimeoutError:
        raise CannotConnect("Connection timeout")
    except Exception as err:
        _LOGGER.exception("Unexpected error validating connection: %s", err)
        raise CannotConnect(f"Unexpected error: {err}")


class RacklinkConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Middle Atlantic RackLink."""

    VERSION = 1

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)

                # Use serial number as the unique ID if available
                if info.get("pdu_serial"):
                    await self.async_set_unique_id(info["pdu_serial"])
                    self._abort_if_unique_id_configured()
                else:
                    # If no serial, use host/port combination
                    unique_id = f"{user_input[CONF_HOST]}:{user_input.get(CONF_PORT, DEFAULT_PORT)}"
                    await self.async_set_unique_id(unique_id)
                    self._abort_if_unique_id_configured()

                # Create the config entry
                title = (
                    info.get("pdu_name")
                    or user_input.get(CONF_PDU_NAME)
                    or f"RackLink PDU ({user_input[CONF_HOST]})"
                )
                return self.async_create_entry(title=title, data=user_input)

            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
