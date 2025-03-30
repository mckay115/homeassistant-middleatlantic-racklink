"""Main controller class for Middle Atlantic RackLink integration."""

import logging
from typing import Any, Dict, Optional

from .base import BaseController
from .config import ConfigMixin

_LOGGER = logging.getLogger(__name__)


class RacklinkController(BaseController, ConfigMixin):
    """Controller for Middle Atlantic RackLink PDU."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        pdu_name: str = None,
        connection_timeout: int = None,
        command_timeout: int = None,
    ) -> None:
        """Initialize the controller with all required information."""
        # Initialize the base controller
        BaseController.__init__(
            self,
            host=host,
            port=port,
            username=username,
            password=password,
            pdu_name=pdu_name,
            connection_timeout=connection_timeout,
            command_timeout=command_timeout,
        )

        _LOGGER.info(
            "Initialized RackLink controller for %s:%s (%s)",
            host,
            port,
            pdu_name or "Unnamed PDU",
        )

    async def async_turn_outlet_on(self, outlet: int) -> bool:
        """Turn on an outlet. Returns True if successful."""
        if not self.connected:
            _LOGGER.warning("Cannot turn on outlet %d: not connected", outlet)
            return False

        try:
            # Try different command formats based on the device type
            commands_to_try = [
                f"on {outlet}",  # Simple format
                f"outlet {outlet} on",  # Newer format
                f"set outlet {outlet} state on",  # Full syntax
            ]

            success = False
            for cmd in commands_to_try:
                try:
                    _LOGGER.debug("Turning on outlet %d with command: %s", outlet, cmd)
                    response = await self.queue_command(cmd)

                    # Check if command was successful
                    if "success" in response.lower() or "on" in response.lower():
                        success = True
                        break

                except Exception as err:
                    _LOGGER.debug(
                        "Error turning on outlet %d with command %s: %s",
                        outlet,
                        cmd,
                        err,
                    )
                    continue

            if success:
                # Update the outlet state in our local cache
                self.outlet_states[outlet] = True
                _LOGGER.info("Successfully turned on outlet %d", outlet)
                return True
            else:
                _LOGGER.warning("Failed to turn on outlet %d with any command", outlet)
                return False

        except Exception as err:
            _LOGGER.error("Error turning on outlet %d: %s", outlet, err)
            return False

    async def async_turn_outlet_off(self, outlet: int) -> bool:
        """Turn off an outlet. Returns True if successful."""
        if not self.connected:
            _LOGGER.warning("Cannot turn off outlet %d: not connected", outlet)
            return False

        try:
            # Try different command formats based on the device type
            commands_to_try = [
                f"off {outlet}",  # Simple format
                f"outlet {outlet} off",  # Newer format
                f"set outlet {outlet} state off",  # Full syntax
            ]

            success = False
            for cmd in commands_to_try:
                try:
                    _LOGGER.debug("Turning off outlet %d with command: %s", outlet, cmd)
                    response = await self.queue_command(cmd)

                    # Check if command was successful
                    if "success" in response.lower() or "off" in response.lower():
                        success = True
                        break

                except Exception as err:
                    _LOGGER.debug(
                        "Error turning off outlet %d with command %s: %s",
                        outlet,
                        cmd,
                        err,
                    )
                    continue

            if success:
                # Update the outlet state in our local cache
                self.outlet_states[outlet] = False
                _LOGGER.info("Successfully turned off outlet %d", outlet)
                return True
            else:
                _LOGGER.warning("Failed to turn off outlet %d with any command", outlet)
                return False

        except Exception as err:
            _LOGGER.error("Error turning off outlet %d: %s", outlet, err)
            return False
