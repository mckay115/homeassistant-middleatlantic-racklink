"""Coordinator for the Middle Atlantic RackLink integration."""

import logging
from datetime import timedelta
from typing import Any, Dict

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .controller.racklink_controller import RacklinkController

_LOGGER = logging.getLogger(__name__)


class RacklinkCoordinator(DataUpdateCoordinator):
    """Coordinator to manage data updates from the RackLink controller."""

    def __init__(
        self,
        hass: HomeAssistant,
        controller: RacklinkController,
        scan_interval: int = 30,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Middle Atlantic RackLink",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.controller = controller
        self._data = {
            "outlets": {},
            "system": {},
            "status": {},
        }

    @property
    def available(self) -> bool:
        """Return True if the controller is available."""
        return self.controller.connected and self.controller.available

    @property
    def device_info(self) -> Dict[str, Any]:
        """Return device information."""
        return {
            "identifiers": {("middle_atlantic_racklink", self.controller.pdu_serial)},
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

    @property
    def outlet_data(self) -> Dict[int, Dict[str, Any]]:
        """Return outlet data."""
        if "outlets" in self.data:
            return self.data["outlets"]
        return {}

    @property
    def system_data(self) -> Dict[str, Any]:
        """Return system power data."""
        if "system" in self.data:
            return self.data["system"]
        return {}

    @property
    def status_data(self) -> Dict[str, Any]:
        """Return status information."""
        if "status" in self.data:
            return self.data["status"]
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
            for outlet_num, state in self.controller.outlet_states.items():
                outlets[outlet_num] = {
                    "state": state,
                    "name": self.controller.outlet_names.get(
                        outlet_num, f"Outlet {outlet_num}"
                    ),
                }

            # Process system power data
            system = {
                "voltage": self.controller.rms_voltage,
                "current": self.controller.rms_current,
                "power": self.controller.active_power,
                "energy": self.controller.active_energy,
                "frequency": self.controller.line_frequency,
            }

            # Process status information
            status = {
                "load_shedding_active": self.controller.load_shedding_active,
                "sequence_active": self.controller.sequence_active,
            }

            # Build the complete data structure
            data = {
                "outlets": outlets,
                "system": system,
                "status": status,
            }

            self._data = data
            return data

        except Exception as err:
            _LOGGER.error("Coordinator: Error during update: %s", err)
            raise UpdateFailed(f"Error communicating with PDU: {err}")

    async def turn_outlet_on(self, outlet: int) -> None:
        """Turn an outlet on and refresh data."""
        _LOGGER.debug("Coordinator: Turning outlet %d on", outlet)
        await self.controller.turn_outlet_on(outlet)
        await self.async_refresh()

    async def turn_outlet_off(self, outlet: int) -> None:
        """Turn an outlet off and refresh data."""
        _LOGGER.debug("Coordinator: Turning outlet %d off", outlet)
        await self.controller.turn_outlet_off(outlet)
        await self.async_refresh()

    async def cycle_outlet(self, outlet: int) -> None:
        """Cycle an outlet and refresh data."""
        _LOGGER.debug("Coordinator: Cycling outlet %d", outlet)
        await self.controller.cycle_outlet(outlet)
        await self.async_refresh()

    async def cycle_all_outlets(self) -> None:
        """Cycle all outlets and refresh data."""
        _LOGGER.debug("Coordinator: Cycling all outlets")
        await self.controller.cycle_all_outlets()
        await self.async_refresh()

    async def start_load_shedding(self) -> None:
        """Start load shedding and refresh data."""
        _LOGGER.debug("Coordinator: Starting load shedding")
        await self.controller.start_load_shedding()
        await self.async_refresh()

    async def stop_load_shedding(self) -> None:
        """Stop load shedding and refresh data."""
        _LOGGER.debug("Coordinator: Stopping load shedding")
        await self.controller.stop_load_shedding()
        await self.async_refresh()

    async def start_sequence(self) -> None:
        """Start the outlet sequence and refresh data."""
        _LOGGER.debug("Coordinator: Starting outlet sequence")
        await self.controller.start_sequence()
        await self.async_refresh()

    async def stop_sequence(self) -> None:
        """Stop the outlet sequence and refresh data."""
        _LOGGER.debug("Coordinator: Stopping outlet sequence")
        await self.controller.stop_sequence()
        await self.async_refresh()
