"""Coordinator for the Middle Atlantic RackLink integration."""

import logging
from datetime import timedelta
from typing import Any, Dict, Optional

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
            "sensors": {},
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
            "name": self.controller.pdu_name,
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
    def sensor_data(self) -> Dict[str, Any]:
        """Return sensor data."""
        if "sensors" in self.data:
            return self.data["sensors"]
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

            # Process the data for outlets
            outlets = {}
            for outlet_num, state in self.controller.outlet_states.items():
                outlets[outlet_num] = {
                    "state": state,
                    "name": self.controller.outlet_names.get(
                        outlet_num, f"Outlet {outlet_num}"
                    ),
                    "power": self.controller.outlet_power.get(outlet_num),
                    "current": self.controller.outlet_current.get(outlet_num),
                    "voltage": self.controller.outlet_voltage.get(outlet_num),
                    "energy": self.controller.outlet_energy.get(outlet_num),
                    "power_factor": self.controller.outlet_power_factor.get(outlet_num),
                    "frequency": self.controller.outlet_line_frequency.get(outlet_num),
                }

            # Process the data for sensors
            sensors = self.controller.sensors.copy()

            # Build the complete data structure
            data = {
                "outlets": outlets,
                "sensors": sensors,
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

    async def set_outlet_name(self, outlet: int, name: str) -> None:
        """Set the name of an outlet and refresh data."""
        _LOGGER.debug("Coordinator: Setting outlet %d name to '%s'", outlet, name)
        await self.controller.set_outlet_name(outlet, name)
        await self.async_refresh()

    async def set_pdu_name(self, name: str) -> None:
        """Set the name of the PDU and refresh data."""
        _LOGGER.debug("Coordinator: Setting PDU name to '%s'", name)
        await self.controller.set_pdu_name(name)
        await self.async_refresh()
