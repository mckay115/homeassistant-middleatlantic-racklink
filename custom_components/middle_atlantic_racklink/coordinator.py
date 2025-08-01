"""Data update coordinator for the Middle Atlantic RackLink integration."""

# Standard library imports
import asyncio
from datetime import timedelta
import logging
from typing import Any, Dict

# Home Assistant core imports
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

# Local application/library specific imports
from .const import DOMAIN
from .controller.racklink_controller import RacklinkController

_LOGGER = logging.getLogger(__name__)

# Default polling interval in seconds
DEFAULT_POLLING_INTERVAL = timedelta(seconds=5)


class RacklinkCoordinator(DataUpdateCoordinator):
    """Coordinator to manage data updates from the RackLink controller."""

    def __init__(
        self,
        hass: HomeAssistant,
        controller: RacklinkController,
        update_interval: timedelta = DEFAULT_POLLING_INTERVAL,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )
        self.controller = controller
        self._data: Dict[str, Any] = {}
        self._initialized = False

    async def _async_setup(self) -> None:
        """Initialize the coordinator with device setup."""
        _LOGGER.debug("Setting up RackLink coordinator")

        # Connect to the device and perform initial setup
        await self.controller.connect()

        # Mark as initialized
        self._initialized = True
        _LOGGER.info("RackLink coordinator setup completed")
        _LOGGER.info(
            "Initialized RackLink coordinator with scan interval: %d seconds",
            self.update_interval.total_seconds() if self.update_interval else 0,
        )

    @property
    def available(self) -> bool:
        """Return True if the controller is available."""
        available = self.controller.connected and self.controller.available
        _LOGGER.debug("Coordinator availability status: %s", available)
        return available

    @property
    def device_info(self) -> Dict[str, Any]:
        """Return device information."""
        device_info = {
            "identifiers": {
                ("middle_atlantic_racklink", self.controller.pdu_serial or "unknown")
            },
            "name": self.controller.pdu_name or "RackLink PDU",
            "manufacturer": "Legrand - Middle Atlantic",
            "model": self.controller.pdu_model or "RackLink PDU",
            "sw_version": self.controller.pdu_firmware,
            "connections": (
                {("mac", self.controller.mac_address)}
                if self.controller.mac_address
                else None
            ),
        }
        _LOGGER.debug("Device info: %r", device_info)
        return device_info

    def get_model_capabilities(self) -> Dict[str, Any]:
        """Return model capabilities based on available data.

        Returns:
            Dict containing model capabilities like number of outlets, features, etc.
        """
        capabilities = {
            "num_outlets": 8,  # Default assumption for RackLink devices
            "has_surge_protection": True,  # Most RackLink devices have surge protection
            "has_current_monitoring": True,
            "has_power_monitoring": True,
            "has_energy_monitoring": True,
            "supports_outlet_control": True,
        }

        # Try to determine actual outlet count from controller data
        if hasattr(self.controller, "outlet_states") and self.controller.outlet_states:
            actual_outlets = len(self.controller.outlet_states)
            if actual_outlets > 0:
                capabilities["num_outlets"] = actual_outlets
                _LOGGER.debug(
                    "Detected %d outlets from controller data", actual_outlets
                )

        # Check model-specific capabilities if we have model info
        if self.controller.pdu_model:
            model = self.controller.pdu_model.upper()
            if "920" in model:  # RLNK-P920R series
                capabilities["num_outlets"] = 8
            elif "424" in model:  # Smaller models
                capabilities["num_outlets"] = 4
            elif "1600" in model or "16" in model:  # Larger models
                capabilities["num_outlets"] = 16

        _LOGGER.info("Model capabilities determined: %s", capabilities)
        return capabilities

    @property
    def outlet_data(self) -> Dict[int, Dict[str, Any]]:
        """Return outlet data."""
        if "outlets" in self.data:
            return self.data["outlets"]
        _LOGGER.debug("No outlet data found in coordinator data")
        return {}

    @property
    def system_data(self) -> Dict[str, Any]:
        """Return system power data."""
        if "system" in self.data:
            return self.data["system"]
        _LOGGER.debug("No system data found in coordinator data")
        return {}

    @property
    def status_data(self) -> Dict[str, Any]:
        """Return status information."""
        if "status" in self.data:
            return self.data["status"]
        _LOGGER.debug("No status data found in coordinator data")
        return {}

    async def _async_update_data(self) -> Dict[str, Any]:
        """Update data from the PDU."""
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                # Check connection status and try to connect if needed
                if not self.controller.connected:
                    _LOGGER.debug(
                        "Coordinator: Controller not connected (attempt %d/%d), connecting...",
                        retry_count + 1,
                        max_retries,
                    )
                    if not await self.controller.connect():
                        retry_count += 1
                        if retry_count >= max_retries:
                            _LOGGER.error(
                                "Coordinator: Failed to connect to PDU after %d attempts",
                                max_retries,
                            )
                            raise UpdateFailed(
                                "Failed to connect to PDU after multiple attempts"
                            )
                        else:
                            _LOGGER.warning(
                                "Coordinator: Connection attempt %d failed, retrying...",
                                retry_count,
                            )
                            await asyncio.sleep(2)  # Wait before retry
                            continue

                # Update the PDU data
                _LOGGER.debug("Coordinator: Updating PDU data")
                success = await self.controller.update()

                if not success:
                    # If update failed, the connection might be stale
                    _LOGGER.warning(
                        "Coordinator: Failed to update PDU data, connection may be stale"
                    )
                    await self.controller.disconnect()
                    retry_count += 1
                    if retry_count >= max_retries:
                        raise UpdateFailed(
                            "Failed to update PDU data after multiple attempts"
                        )
                    else:
                        _LOGGER.warning(
                            "Coordinator: Update attempt %d failed, retrying...",
                            retry_count,
                        )
                        await asyncio.sleep(2)  # Wait before retry
                        continue

                # If we get here, the update was successful
                break

            except Exception as err:
                _LOGGER.error(
                    "Coordinator: Error during update attempt %d: %s",
                    retry_count + 1,
                    err,
                )
                retry_count += 1
                if retry_count >= max_retries:
                    raise UpdateFailed(
                        f"Error communicating with PDU after {max_retries} attempts: {err}"
                    ) from err
                else:
                    # Disconnect and retry
                    await self.controller.disconnect()
                    await asyncio.sleep(2)
                    continue

        # Process outlet data (outside the retry loop)
        outlets = {}
        _LOGGER.debug("Processing outlet states: %r", self.controller.outlet_states)
        _LOGGER.debug("Processing outlet names: %r", self.controller.outlet_names)

        for outlet_num, state in self.controller.outlet_states.items():
            outlets[outlet_num] = {
                "state": state,
                "name": self.controller.outlet_names.get(
                    outlet_num, f"Outlet {outlet_num}"
                ),
            }
            _LOGGER.debug(
                "Processed outlet %d: state=%s, name=%r",
                outlet_num,
                "ON" if state else "OFF",
                outlets[outlet_num]["name"],
            )

        _LOGGER.debug("Coordinator: Updated %d outlets", len(outlets))

        # Process system power data
        system = {
            "voltage": self.controller.rms_voltage,
            "current": self.controller.rms_current,
            "power": self.controller.active_power,
            "energy": self.controller.active_energy,
            "frequency": self.controller.line_frequency,
        }

        _LOGGER.info("📊 Coordinator: Updated system data: %r", system)

        # Process status information
        status = {
            "load_shedding_active": self.controller.load_shedding_active,
            "sequence_active": self.controller.sequence_active,
        }

        _LOGGER.debug("Coordinator: Updated status data: %r", status)

        # Build the complete data structure
        data = {
            "outlets": outlets,
            "system": system,
            "status": status,
        }

        _LOGGER.debug("Full updated data: %r", data)
        self._data = data
        return data

    async def turn_outlet_on(self, outlet: int) -> None:
        """Turn an outlet on and refresh data."""
        _LOGGER.info("Coordinator: Turning outlet %d on", outlet)
        success = await self.controller.turn_outlet_on(outlet)

        if success:
            # Update our local data to reflect the change before refresh
            if "outlets" in self._data and outlet in self._data["outlets"]:
                _LOGGER.debug(
                    "Updating local outlet %d state from %s to ON",
                    outlet,
                    "ON" if self._data["outlets"][outlet]["state"] else "OFF",
                )
                self._data["outlets"][outlet]["state"] = True

            # Ensure we update the entity state right away
            _LOGGER.debug("Pushing updated data to entities")
            self.async_set_updated_data(self._data)

            # Do a full refresh to ensure the data is accurate
            _LOGGER.debug("Scheduling a full data refresh")
            await self.async_request_refresh()
        else:
            _LOGGER.warning("Coordinator: Failed to turn outlet %d on", outlet)
            # Force a refresh to get the current state
            _LOGGER.debug("Scheduling a full data refresh after failed command")
            await self.async_request_refresh()

    async def turn_outlet_off(self, outlet: int) -> None:
        """Turn an outlet off and refresh data."""
        _LOGGER.info("Coordinator: Turning outlet %d off", outlet)
        success = await self.controller.turn_outlet_off(outlet)

        if success:
            # Update our local data to reflect the change before refresh
            if "outlets" in self._data and outlet in self._data["outlets"]:
                _LOGGER.debug(
                    "Updating local outlet %d state from %s to OFF",
                    outlet,
                    "ON" if self._data["outlets"][outlet]["state"] else "OFF",
                )
                self._data["outlets"][outlet]["state"] = False

            # Ensure we update the entity state right away
            _LOGGER.debug("Pushing updated data to entities")
            self.async_set_updated_data(self._data)

            # Do a full refresh to ensure the data is accurate
            _LOGGER.debug("Scheduling a full data refresh")
            await self.async_request_refresh()
        else:
            _LOGGER.warning("Coordinator: Failed to turn outlet %d off", outlet)
            # Force a refresh to get the current state
            _LOGGER.debug("Scheduling a full data refresh after failed command")
            await self.async_request_refresh()

    async def cycle_outlet(self, outlet: int) -> None:
        """Cycle an outlet and refresh data."""
        _LOGGER.info("Coordinator: Cycling outlet %d", outlet)
        success = await self.controller.cycle_outlet(outlet)

        if success:
            # Outlet will be on after cycling
            if "outlets" in self._data and outlet in self._data["outlets"]:
                _LOGGER.debug(
                    "Updating local outlet %d state to ON after cycle (was %s)",
                    outlet,
                    "ON" if self._data["outlets"][outlet]["state"] else "OFF",
                )
                self._data["outlets"][outlet]["state"] = True

            # Ensure we update the entity state right away
            _LOGGER.debug("Pushing updated data to entities after cycle")
            self.async_set_updated_data(self._data)

            # Force a refresh after cycling to get accurate state
            _LOGGER.debug("Scheduling a full data refresh after cycle")
            await self.async_request_refresh()
        else:
            _LOGGER.warning("Coordinator: Failed to cycle outlet %d", outlet)
            _LOGGER.debug("Scheduling a full data refresh after failed cycle command")
            await self.async_request_refresh()

    async def cycle_all_outlets(self) -> None:
        """Cycle all outlets and refresh data."""
        _LOGGER.info("Coordinator: Cycling all outlets")
        success = await self.controller.cycle_all_outlets()

        if success:
            # Update all outlets to on
            if "outlets" in self._data:
                _LOGGER.debug("Updating all local outlet states to ON after cycle")
                for outlet in self._data["outlets"]:
                    self._data["outlets"][outlet]["state"] = True

            # Ensure we update the entity states right away
            _LOGGER.debug("Pushing updated data to entities after cycle all")
            self.async_set_updated_data(self._data)

            # Force a refresh after cycling to get accurate state
            _LOGGER.debug("Scheduling a full data refresh after cycling all outlets")
            await self.async_request_refresh()
        else:
            _LOGGER.warning("Coordinator: Failed to cycle all outlets")
            _LOGGER.debug(
                "Scheduling a full data refresh after failed cycle all command"
            )
            await self.async_request_refresh()

    async def start_load_shedding(self) -> None:
        """Start load shedding and refresh data."""
        _LOGGER.info("Coordinator: Starting load shedding")
        success = await self.controller.start_load_shedding()

        if success:
            # Update status in local data
            if "status" in self._data:
                _LOGGER.debug(
                    "Updating local load_shedding_active state to True (was %s)",
                    self._data["status"].get("load_shedding_active", False),
                )
                self._data["status"]["load_shedding_active"] = True

            # Ensure we update the entity states right away
            _LOGGER.debug("Pushing updated data to entities after load shedding start")
            self.async_set_updated_data(self._data)

            # Refresh data to get accurate state
            _LOGGER.debug("Scheduling a full data refresh after starting load shedding")
            await self.async_request_refresh()
        else:
            _LOGGER.warning("Coordinator: Failed to start load shedding")
            _LOGGER.debug(
                "Scheduling a full data refresh after failed load shedding start"
            )
            await self.async_request_refresh()

    async def stop_load_shedding(self) -> None:
        """Stop load shedding and refresh data."""
        _LOGGER.info("Coordinator: Stopping load shedding")
        success = await self.controller.stop_load_shedding()

        if success:
            # Update status in local data
            if "status" in self._data:
                _LOGGER.debug(
                    "Updating local load_shedding_active state to False (was %s)",
                    self._data["status"].get("load_shedding_active", False),
                )
                self._data["status"]["load_shedding_active"] = False

            # Ensure we update the entity states right away
            _LOGGER.debug("Pushing updated data to entities after load shedding stop")
            self.async_set_updated_data(self._data)

            # Refresh data to get accurate state
            _LOGGER.debug("Scheduling a full data refresh after stopping load shedding")
            await self.async_request_refresh()
        else:
            _LOGGER.warning("Coordinator: Failed to stop load shedding")
            _LOGGER.debug(
                "Scheduling a full data refresh after failed load shedding stop"
            )
            await self.async_request_refresh()

    async def start_sequence(self) -> None:
        """Start the outlet sequence and refresh data."""
        _LOGGER.info("Coordinator: Starting outlet sequence")
        success = await self.controller.start_sequence()

        if success:
            # Update status in local data
            if "status" in self._data:
                _LOGGER.debug(
                    "Updating local sequence_active state to True (was %s)",
                    self._data["status"].get("sequence_active", False),
                )
                self._data["status"]["sequence_active"] = True

            # Ensure we update the entity states right away
            _LOGGER.debug("Pushing updated data to entities after sequence start")
            self.async_set_updated_data(self._data)

            # Refresh data to get accurate state
            _LOGGER.debug("Scheduling a full data refresh after starting sequence")
            await self.async_request_refresh()
        else:
            _LOGGER.warning("Coordinator: Failed to start outlet sequence")
            _LOGGER.debug("Scheduling a full data refresh after failed sequence start")
            await self.async_request_refresh()

    async def stop_sequence(self) -> None:
        """Stop the outlet sequence and refresh data."""
        _LOGGER.info("Coordinator: Stopping outlet sequence")
        success = await self.controller.stop_sequence()

        if success:
            # Update status in local data
            if "status" in self._data:
                _LOGGER.debug(
                    "Updating local sequence_active state to False (was %s)",
                    self._data["status"].get("sequence_active", False),
                )
                self._data["status"]["sequence_active"] = False

            # Ensure we update the entity states right away
            _LOGGER.debug("Pushing updated data to entities after sequence stop")
            self.async_set_updated_data(self._data)

            # Refresh data to get accurate state
            _LOGGER.debug("Scheduling a full data refresh after stopping sequence")
            await self.async_request_refresh()
        else:
            _LOGGER.warning("Coordinator: Failed to stop outlet sequence")
            _LOGGER.debug("Scheduling a full data refresh after failed sequence stop")
            await self.async_request_refresh()

    async def test_direct_commands(self) -> str:
        """Test direct commands with the RackLink device.

        This is a debug method to attempt various command syntaxes.
        """
        _LOGGER.info("Coordinator: Running direct command tests")
        return await self.controller.test_direct_commands()
