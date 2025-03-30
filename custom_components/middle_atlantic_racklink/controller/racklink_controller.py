"""Main controller class for Middle Atlantic RackLink integration."""

import asyncio
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

        self._background_task = None
        self._shutdown = False

        _LOGGER.info(
            "Initialized RackLink controller for %s:%s (%s)",
            host,
            port,
            pdu_name or "Unnamed PDU",
        )

    async def start_background_connection(self) -> None:
        """Start background connection task."""
        if self._background_task is None or self._background_task.done():
            self._shutdown = False
            self._background_task = asyncio.create_task(
                self._background_connection_loop()
            )
            _LOGGER.debug("Started background connection task")

    async def shutdown(self) -> None:
        """Shutdown the controller and all background tasks."""
        self._shutdown = True
        if self._background_task and not self._background_task.done():
            try:
                self._background_task.cancel()
                await self._background_task
            except asyncio.CancelledError:
                pass
            except Exception as err:
                _LOGGER.error("Error cancelling background task: %s", err)

        await self.disconnect()
        _LOGGER.debug("Controller shutdown complete")

    async def _background_connection_loop(self) -> None:
        """Background task that maintains connection to the PDU."""
        retry_delay = 10  # Start with 10 seconds
        max_retry_delay = 300  # Cap at 5 minutes

        while not self._shutdown:
            try:
                if not self.connected:
                    _LOGGER.debug("Attempting to connect in background task")
                    if await self.connect():
                        _LOGGER.info("Successfully connected to %s", self.host)
                        retry_delay = 10  # Reset delay on successful connection
                    else:
                        # Increase retry delay with exponential backoff
                        retry_delay = min(retry_delay * 1.5, max_retry_delay)
                        _LOGGER.debug(
                            "Connection failed, retrying in %d seconds", retry_delay
                        )

                # Wait before checking connection again
                await asyncio.sleep(retry_delay)

            except asyncio.CancelledError:
                _LOGGER.debug("Background connection task cancelled")
                break
            except Exception as err:
                _LOGGER.error("Error in background connection task: %s", err)
                # Wait before trying again after error
                await asyncio.sleep(retry_delay)
                # Increase retry delay with exponential backoff
                retry_delay = min(retry_delay * 1.5, max_retry_delay)

        _LOGGER.debug("Background connection task ended")

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
