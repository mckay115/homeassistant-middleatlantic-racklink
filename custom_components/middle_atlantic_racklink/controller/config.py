"""Configuration functionality for the RackLink controller."""

import asyncio
import logging
from typing import Any, Dict, Optional, Set, Union

_LOGGER = logging.getLogger(__name__)


class ConfigMixin:
    """Configuration methods for RackLink devices."""

    async def set_outlet_name(self, outlet: int, name: str) -> bool:
        """Set the name of an outlet. Returns True if successful."""
        if not hasattr(self, "connected") or not self.connected:
            _LOGGER.warning("Cannot set outlet name, not connected")
            return False

        if not name:
            _LOGGER.warning("Cannot set empty outlet name")
            return False

        # Clean the name - only alphanumeric and spaces
        safe_name = "".join(c for c in name if c.isalnum() or c.isspace())
        if not safe_name:
            _LOGGER.warning("Name contains no valid characters")
            return False

        # Truncate if too long
        if len(safe_name) > 20:
            _LOGGER.debug("Truncating outlet name to 20 characters")
            safe_name = safe_name[:20]

        try:
            _LOGGER.debug("Setting outlet %d name to '%s'", outlet, safe_name)

            # Try different command formats
            commands = [
                f"set outlet {outlet} name {safe_name}",
                f"config outlet {outlet} name {safe_name}",
                f"config outlets {outlet} name {safe_name}",
            ]

            success = False
            for cmd in commands:
                try:
                    response = await self.queue_command(cmd)

                    # Check for success indicators
                    if (
                        "success" in response.lower()
                        or "configured" in response.lower()
                    ):
                        success = True
                        # Some devices need to apply the config
                        await self._try_apply_config()
                        break

                except Exception as err:
                    _LOGGER.debug(
                        "Error setting outlet %d name with command %s: %s",
                        outlet,
                        cmd,
                        err,
                    )
                    continue

            if success:
                # Update the outlet name in our cache
                if hasattr(self, "outlet_names"):
                    self.outlet_names[outlet] = safe_name
                _LOGGER.info(
                    "Successfully set outlet %d name to '%s'", outlet, safe_name
                )
                return True
            else:
                _LOGGER.warning("Failed to set outlet %d name", outlet)
                return False

        except Exception as err:
            _LOGGER.error("Error setting outlet %d name: %s", outlet, err)
            return False

    async def set_pdu_name(self, name: str) -> bool:
        """Set the name of the PDU. Returns True if successful."""
        if not hasattr(self, "connected") or not self.connected:
            _LOGGER.warning("Cannot set PDU name, not connected")
            return False

        if not name:
            _LOGGER.warning("Cannot set empty PDU name")
            return False

        # Clean the name - only alphanumeric and spaces
        safe_name = "".join(c for c in name if c.isalnum() or c.isspace())
        if not safe_name:
            _LOGGER.warning("Name contains no valid characters")
            return False

        # Truncate if too long
        if len(safe_name) > 20:
            _LOGGER.debug("Truncating PDU name to 20 characters")
            safe_name = safe_name[:20]

        try:
            _LOGGER.debug("Setting PDU name to '%s'", safe_name)

            # Try different command formats
            commands = [
                f"set pdu name {safe_name}",
                f"set device name {safe_name}",
                f"config pdu name {safe_name}",
                f"config device name {safe_name}",
            ]

            success = False
            for cmd in commands:
                try:
                    response = await self.queue_command(cmd)

                    # Check for success indicators
                    if (
                        "success" in response.lower()
                        or "configured" in response.lower()
                    ):
                        success = True
                        # Some devices need to apply the config
                        await self._try_apply_config()
                        break

                except Exception as err:
                    _LOGGER.debug(
                        "Error setting PDU name with command %s: %s", cmd, err
                    )
                    continue

            if success:
                # Update the PDU name in our object
                if hasattr(self, "_pdu_name"):
                    self._pdu_name = safe_name
                _LOGGER.info("Successfully set PDU name to '%s'", safe_name)
                return True
            else:
                _LOGGER.warning("Failed to set PDU name")
                return False

        except Exception as err:
            _LOGGER.error("Error setting PDU name: %s", err)
            return False

    async def turn_outlet_on(self, outlet: int) -> bool:
        """Turn on an outlet. Returns True if successful."""
        if not hasattr(self, "async_turn_outlet_on"):
            _LOGGER.error("turn_outlet_on method not implemented in main controller")
            return False

        return await self.async_turn_outlet_on(outlet)

    async def turn_outlet_off(self, outlet: int) -> bool:
        """Turn off an outlet. Returns True if successful."""
        if not hasattr(self, "async_turn_outlet_off"):
            _LOGGER.error("turn_outlet_off method not implemented in main controller")
            return False

        return await self.async_turn_outlet_off(outlet)

    async def cycle_outlet(self, outlet: int) -> bool:
        """Cycle power for an outlet. Returns True if successful."""
        if not hasattr(self, "connected") or not self.connected:
            _LOGGER.warning("Cannot cycle outlet, not connected")
            return False

        try:
            _LOGGER.debug("Cycling outlet %d", outlet)

            # Try different command formats
            commands = [
                f"cycle {outlet}",
                f"outlet {outlet} cycle",
                f"cycle outlet {outlet}",
                f"reboot outlet {outlet}",
            ]

            success = False
            for cmd in commands:
                try:
                    response = await self.queue_command(cmd)

                    # Check for success indicators
                    if any(
                        x in response.lower()
                        for x in ["success", "cycled", "cycling", "reboot"]
                    ):
                        success = True
                        break

                except Exception as err:
                    _LOGGER.debug(
                        "Error cycling outlet %d with command %s: %s", outlet, cmd, err
                    )
                    continue

            if success:
                _LOGGER.info("Successfully cycled outlet %d", outlet)
                # Update the state to reflect cycling (will be off initially)
                if hasattr(self, "outlet_states"):
                    self.outlet_states[outlet] = False
                return True
            else:
                _LOGGER.warning("Failed to cycle outlet %d with any command", outlet)
                # Try the manual approach (off then on)
                if hasattr(self, "async_turn_outlet_off") and hasattr(
                    self, "async_turn_outlet_on"
                ):
                    _LOGGER.debug("Trying manual cycle for outlet %d", outlet)
                    if await self.async_turn_outlet_off(outlet):
                        await asyncio.sleep(2)  # Wait 2 seconds
                        if await self.async_turn_outlet_on(outlet):
                            _LOGGER.info(
                                "Successfully manually cycled outlet %d", outlet
                            )
                            return True
                return False

        except Exception as err:
            _LOGGER.error("Error cycling outlet %d: %s", outlet, err)
            return False

    async def cycle_all_outlets(self) -> bool:
        """Cycle power for all outlets. Returns True if at least one outlet was cycled."""
        if not hasattr(self, "connected") or not self.connected:
            _LOGGER.warning("Cannot cycle all outlets, not connected")
            return False

        try:
            _LOGGER.debug("Cycling all outlets")

            # Try the 'cycle all' command first
            try:
                commands = [
                    "cycle all",
                    "cycle outlets all",
                    "reboot all",
                ]

                for cmd in commands:
                    try:
                        response = await self.queue_command(cmd)
                        if any(
                            x in response.lower()
                            for x in ["success", "cycled", "cycling", "reboot"]
                        ):
                            _LOGGER.info(
                                "Successfully cycled all outlets with command: %s", cmd
                            )
                            return True
                    except Exception:
                        continue
            except Exception as err:
                _LOGGER.debug("Error with cycle all command: %s", err)

            # If that fails, cycle each outlet individually
            if hasattr(self, "outlet_states") and self.outlet_states:
                cycled_count = 0
                for outlet in self.outlet_states:
                    if await self.cycle_outlet(outlet):
                        cycled_count += 1
                        await asyncio.sleep(0.5)  # Small delay between outlets

                if cycled_count > 0:
                    _LOGGER.info(
                        "Successfully cycled %d outlets individually", cycled_count
                    )
                    return True
                else:
                    _LOGGER.warning("Failed to cycle any outlets")
                    return False
            else:
                _LOGGER.warning("No outlets found to cycle")
                return False

        except Exception as err:
            _LOGGER.error("Error cycling all outlets: %s", err)
            return False

    async def _try_apply_config(self) -> None:
        """Try to apply configuration changes if needed."""
        if not hasattr(self, "connected") or not self.connected:
            return

        try:
            # Some devices need an explicit 'apply' command after config changes
            commands = ["apply", "apply config", "commit"]

            for cmd in commands:
                try:
                    _LOGGER.debug("Trying to apply configuration with: %s", cmd)
                    response = await self.queue_command(cmd)
                    if any(
                        x in response.lower()
                        for x in ["success", "applied", "committed"]
                    ):
                        _LOGGER.debug("Successfully applied configuration")
                        break
                except Exception:
                    continue

        except Exception as err:
            _LOGGER.debug("Error applying configuration: %s", err)
