"""Coordinator for the Middle Atlantic RackLink integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

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
        _LOGGER.info(
            "Initialized RackLink coordinator with scan interval: %d seconds",
            update_interval.total_seconds(),
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
        try:
            # Check connection status and try to connect if needed
            if not self.controller.connected:
                _LOGGER.debug("Coordinator: Controller not connected, connecting...")
                if not await self.controller.connect():
                    _LOGGER.error("Coordinator: Failed to connect to PDU")
                    raise UpdateFailed("Failed to connect to PDU")

            # Update the PDU data
            _LOGGER.debug("Coordinator: Updating PDU data")
            success = await self.controller.update()

            if not success:
                _LOGGER.warning("Coordinator: Failed to update PDU data")
                raise UpdateFailed("Failed to update PDU data")

            # Process outlet data
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

            _LOGGER.debug("Coordinator: Updated system data: %r", system)

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

        except Exception as err:
            _LOGGER.error("Coordinator: Error during update: %s", err)
            raise UpdateFailed(f"Error communicating with PDU: {err}") from err

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
