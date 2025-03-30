"""Config flow for Middle Atlantic Racklink integration."""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_MODEL,
    DEFAULT_PORT,
    DOMAIN,
    MODEL_DESCRIPTIONS,
    SUPPORTED_MODELS,
)
from .racklink_controller import RacklinkController

_LOGGER = logging.getLogger(__name__)
CONNECTION_TIMEOUT = 15  # Timeout in seconds for connection validation


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
            # Use timeout for connection to prevent UI from hanging
            _LOGGER.debug(
                "Attempting to connect to %s with timeout %s seconds",
                user_input[CONF_HOST],
                CONNECTION_TIMEOUT,
            )

            try:
                connect_success = await asyncio.wait_for(
                    controller.connect(), timeout=CONNECTION_TIMEOUT
                )
                if not connect_success:
                    _LOGGER.error(
                        "Failed to connect to %s",
                        user_input[CONF_HOST],
                    )
                    errors["base"] = "cannot_connect"
                    return errors, device_info
            except asyncio.TimeoutError:
                _LOGGER.error(
                    "Connection to %s timed out after %s seconds",
                    user_input[CONF_HOST],
                    CONNECTION_TIMEOUT,
                )
                errors["base"] = "timeout"
                return errors, device_info

            # Try to get device info
            device_info = await controller.get_device_info()

            # We allow minimal device info
            if not device_info:
                device_info = {
                    "model": f"RackLink PDU ({user_input[CONF_HOST]})",
                    "serial": f"RLNK_{user_input[CONF_HOST].replace('.', '_')}",
                    "firmware": "Unknown",
                    "name": f"RackLink PDU ({user_input[CONF_HOST]})",
                }
                _LOGGER.warning(
                    "Using default device info for %s", user_input[CONF_HOST]
                )

            # Verify model selection if not on auto-detect
            if user_input.get(CONF_MODEL) != "AUTO_DETECT":
                detected_model = controller.pdu_model or ""
                selected_model = user_input.get(CONF_MODEL)

                if (
                    selected_model
                    and detected_model
                    and selected_model not in detected_model
                ):
                    _LOGGER.warning(
                        "Selected model %s doesn't match detected model %s. User override accepted.",
                        selected_model,
                        detected_model,
                    )

            _LOGGER.info(
                "Successfully validated connection to %s (%s - %s)",
                user_input[CONF_HOST],
                device_info.get("model", "Unknown"),
                device_info.get("serial", "Unknown"),
            )

            # Use timeout for disconnect as well
            try:
                await asyncio.wait_for(controller.disconnect(), timeout=5)
            except asyncio.TimeoutError:
                _LOGGER.warning("Disconnect timed out, but validation succeeded")

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

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            _LOGGER.debug("Config flow received user input: %s", user_input)
            try:
                # Validate connection
                host = user_input[CONF_HOST]
                port = int(user_input.get(CONF_PORT, DEFAULT_PORT))
                username = user_input.get(CONF_USERNAME, "admin")
                password = user_input[CONF_PASSWORD]

                _LOGGER.debug("Testing connection to %s:%s", host, port)

                valid, info = await self._test_connection(
                    host, port, username, password
                )

                if valid:
                    _LOGGER.info(
                        "Successfully connected to Middle Atlantic Racklink at %s",
                        host,
                    )

                    # Create entry
                    return self.async_create_entry(
                        title=f"Racklink PDU {host}",
                        data={
                            CONF_HOST: host,
                            CONF_PORT: port,
                            CONF_USERNAME: username,
                            CONF_PASSWORD: password,
                            "model": info.get("model", "Unknown"),
                        },
                    )
                else:
                    # Connection failed with specific error
                    _LOGGER.warning(
                        "Connection test failed: %s", info.get("error", "Unknown error")
                    )
                    errors["base"] = info.get("error", "cannot_connect")
            except ConnectionRefusedError:
                _LOGGER.error("Connection refused by %s:%s", host, port)
                errors["base"] = "connection_refused"
            except asyncio.TimeoutError:
                _LOGGER.error("Connection to %s:%s timed out", host, port)
                errors["base"] = "timeout"
            except Exception as ex:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception: %s", ex)
                errors["base"] = "unknown"

        # Prepare default values for form
        default_port = DEFAULT_PORT
        default_username = "admin"

        # Fill in user input or defaults
        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOST,
                    default=user_input.get(CONF_HOST, "") if user_input else "",
                ): str,
                vol.Required(
                    CONF_PORT,
                    default=(
                        user_input.get(CONF_PORT, default_port)
                        if user_input
                        else default_port
                    ),
                ): int,
                vol.Required(
                    CONF_USERNAME,
                    default=(
                        user_input.get(CONF_USERNAME, default_username)
                        if user_input
                        else default_username
                    ),
                ): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    async def _test_connection(self, host, port, username, password):
        """Test if we can connect to the PDU."""
        _LOGGER.debug(
            "Testing connection to %s:%s with username '%s'", host, port, username
        )

        # Initialize controller
        controller = RacklinkController(
            host=host,
            port=port,
            username=username,
            password=password,
            socket_timeout=15.0,  # Increased timeout for initial connection
            login_timeout=20.0,  # Increased login timeout
            command_timeout=15.0,  # Increased command timeout
        )

        try:
            # Start background connection with longer timeout for setup
            _LOGGER.debug("Starting background connection...")
            connection_task = controller.start_background_connection()

            try:
                _LOGGER.debug("Waiting for initial connection (25 second timeout)")
                await asyncio.wait_for(connection_task, timeout=25)
                _LOGGER.debug("Connection established")
            except asyncio.TimeoutError:
                _LOGGER.warning("Initial connection timed out")
                # Don't fail immediately, continue anyway and see if we can get device info

            # Wait for connection to stabilize
            _LOGGER.debug("Waiting 5 seconds for connection to stabilize")
            await asyncio.sleep(5)

            # Try to get device info
            _LOGGER.debug("Getting device info")
            try:
                device_info = await asyncio.wait_for(
                    controller.get_device_info(), timeout=20
                )
                _LOGGER.debug("Device info received: %s", device_info)

                # Check if we got valid device info
                if device_info and "model" in device_info:
                    _LOGGER.info(
                        "Successfully connected to %s - Model: %s",
                        host,
                        device_info.get("model", "Unknown"),
                    )

                    # Try to get basic outlet status to verify command execution
                    _LOGGER.debug("Getting outlet status to verify commands work")
                    try:
                        outlet_cmd = "show outlets all"
                        outlet_response = await asyncio.wait_for(
                            controller.send_command(outlet_cmd), timeout=15
                        )

                        if outlet_response:
                            _LOGGER.debug("Command test succeeded, received response")
                            await controller.disconnect()
                            return True, device_info
                        else:
                            _LOGGER.warning("Command test failed: no response")
                            await controller.disconnect()
                            return False, {"error": "Command test failed: no response"}
                    except Exception as cmd_err:
                        _LOGGER.warning("Command test error: %s", cmd_err)
                        await controller.disconnect()
                        return False, {"error": f"Command test error: {cmd_err}"}

                else:
                    _LOGGER.warning("Failed to get valid device info")
                    await controller.disconnect()
                    return False, {"error": "Could not get device information"}
            except asyncio.TimeoutError:
                _LOGGER.warning("Timeout getting device info")
                await controller.disconnect()
                return False, {"error": "Timeout getting device information"}
            except Exception as err:
                _LOGGER.error("Error getting device info: %s", err)
                await controller.disconnect()
                return False, {"error": f"Error getting device info: {err}"}

        except Exception as err:
            _LOGGER.error("Error connecting to PDU: %s", err)
            try:
                await controller.disconnect()
            except:
                pass
            return False, {"error": f"Connection error: {err}"}

        # We should never reach here, but just in case
        try:
            await controller.disconnect()
        except:
            pass
        return False, {"error": "Unknown connection error"}
