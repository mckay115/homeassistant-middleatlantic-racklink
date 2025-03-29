"""Config flow for Middle Atlantic Racklink integration."""

from __future__ import annotations

import logging
import socket
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv

from .const import DEFAULT_PORT, DOMAIN
from .racklink_controller import RacklinkController

_LOGGER = logging.getLogger(__name__)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Middle Atlantic Racklink."""

    VERSION = 1

    async def validate_input(self, user_input: dict[str, Any]) -> dict[str, Any]:
        """Validate the user input."""
        errors = {}
        device_info = {}

        # Verify host is reachable before attempting connection
        try:
            socket.gethostbyname(user_input[CONF_HOST])
        except socket.gaierror:
            _LOGGER.error("Host %s is not reachable", user_input[CONF_HOST])
            errors["base"] = "cannot_connect"
            return errors, device_info

        controller = RacklinkController(
            user_input[CONF_HOST],
            user_input[CONF_PORT],
            user_input[CONF_USERNAME],
            user_input[CONF_PASSWORD],
        )

        try:
            await controller.connect()
            device_info = {
                "name": controller.pdu_name,
                "model": controller.pdu_model,
                "firmware": controller.pdu_firmware,
                "serial": controller.pdu_serial,
                "mac": controller.pdu_mac,
            }
            _LOGGER.info(
                "Successfully validated connection to %s (%s - %s)",
                user_input[CONF_HOST],
                controller.pdu_model,
                controller.pdu_serial,
            )
            await controller.disconnect()
        except ValueError as err:
            _LOGGER.error(
                "Could not connect to device at %s: %s", user_input[CONF_HOST], err
            )
            errors["base"] = "cannot_connect"
        except (asyncio.TimeoutError, ConnectionRefusedError) as err:
            _LOGGER.error(
                "Timeout connecting to device at %s: %s", user_input[CONF_HOST], err
            )
            errors["base"] = "timeout"
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception: %s", err)
            errors["base"] = "unknown"

        return errors, device_info

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle the initial step."""
        errors = {}
        device_info = {}

        if user_input is not None:
            # Prevent duplicate entries
            await self.async_set_unique_id(
                f"{user_input[CONF_HOST]}_{user_input[CONF_PORT]}"
            )
            self._abort_if_unique_id_configured()

            errors, device_info = await self.validate_input(user_input)

            if not errors:
                title = device_info.get("name", user_input[CONF_HOST])
                return self.async_create_entry(
                    title=title,
                    data=user_input,
                    description=f"Model: {device_info.get('model', 'Unknown')} - SN: {device_info.get('serial', 'Unknown')}",
                )

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOST, description={"suggested_value": "192.168.1.100"}
                ): cv.string,
                vol.Required(
                    CONF_PORT,
                    default=DEFAULT_PORT,
                    description={"suggested_value": DEFAULT_PORT},
                ): cv.port,
                vol.Required(
                    CONF_USERNAME, description={"suggested_value": "admin"}
                ): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
            }
        )

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )
