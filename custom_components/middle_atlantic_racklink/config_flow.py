"""Config flow for Middle Atlantic Racklink integration."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult

from .const import DEFAULT_PORT, DOMAIN
from .racklink_controller import RacklinkController


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Middle Atlantic Racklink."""

    VERSION = 1

    async def validate_input(self, user_input):
        """Validate the user input."""
        controller = RacklinkController(
            user_input[CONF_HOST],
            user_input[CONF_PORT],
            user_input[CONF_USERNAME],
            user_input[CONF_PASSWORD],
        )
        try:
            await controller.connect()
            await controller.disconnect()
        except ValueError as err:
            raise ValueError(f"Could not connect to the device: {err}")

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            try:
                await self.validate_input(user_input)
                return self.async_create_entry(
                    title=user_input[CONF_HOST], data=user_input
                )
            except ValueError as err:
                errors["base"] = "cannot_connect"

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOST, description={"suggested_value": "192.168.1.100"}
                ): str,
                vol.Required(
                    CONF_PORT,
                    default=DEFAULT_PORT,
                    description={"suggested_value": DEFAULT_PORT},
                ): int,
                vol.Required(
                    CONF_USERNAME, description={"suggested_value": "admin"}
                ): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )
