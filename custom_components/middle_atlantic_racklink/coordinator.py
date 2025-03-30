"""Data update coordinator for Middle Atlantic Racklink."""

import asyncio
import logging
from datetime import timedelta
from typing import Any, Dict, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .device import RacklinkDevice

_LOGGER = logging.getLogger(__name__)


class RacklinkDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the device."""

    def __init__(
        self,
        hass: HomeAssistant,
        device: RacklinkDevice,
        name: str,
        update_interval: timedelta = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        """Initialize coordinator."""
        self.device = device
        self.hass = hass

        super().__init__(
            hass,
            _LOGGER,
            name=name,
            update_interval=update_interval,
        )

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from the device."""
        try:
            # Check if device is connected, if not, attempt reconnection
            if not self.device.connected:
                _LOGGER.debug("Device not connected, attempting reconnection")
                try:
                    await self.device.reconnect()

                    # If still not connected, raise exception but let coordinator handle it
                    if not self.device.connected:
                        _LOGGER.warning("Connection not available, will retry later")
                        raise UpdateFailed("Device connection unavailable")
                except Exception as e:
                    _LOGGER.warning("Error during reconnection attempt: %s", e)
                    raise UpdateFailed(f"Connection error: {e}")

            # Check connection again before attempting update
            if self.device.connected:
                _LOGGER.debug("Updating data from device")
                try:
                    # Use a timeout to prevent blocking indefinitely
                    success = await asyncio.wait_for(self.device.update(), timeout=15)

                    if not success:
                        _LOGGER.warning("Device update returned unsuccessful status")
                        # We'll still return data, but log the issue
                except asyncio.TimeoutError:
                    _LOGGER.warning("Data update timed out")
                    raise UpdateFailed("Data update timed out")
                except Exception as e:
                    _LOGGER.error("Error during device update: %s", e)
                    raise UpdateFailed(f"Update error: {e}")

                # Get the latest device info
                device_info = await self.device.get_device_info()

                # Collect updated data for entities
                return {
                    "device_info": device_info,
                    "available": self.device.available,
                    "outlet_states": self.device.outlet_states.copy(),
                    "outlet_names": self.device.outlet_names.copy(),
                    "outlet_power": (
                        self.device.outlet_power.copy()
                        if hasattr(self.device, "outlet_power")
                        else {}
                    ),
                    "outlet_current": (
                        self.device.outlet_current.copy()
                        if hasattr(self.device, "outlet_current")
                        else {}
                    ),
                    "outlet_energy": (
                        self.device.outlet_energy.copy()
                        if hasattr(self.device, "outlet_energy")
                        else {}
                    ),
                    "outlet_voltage": (
                        self.device.outlet_voltage.copy()
                        if hasattr(self.device, "outlet_voltage")
                        else {}
                    ),
                    "outlet_power_factor": (
                        self.device.outlet_power_factor.copy()
                        if hasattr(self.device, "outlet_power_factor")
                        else {}
                    ),
                    "sensors": (
                        self.device.sensors.copy()
                        if hasattr(self.device, "sensors")
                        else {}
                    ),
                    "last_update": getattr(self.device, "_last_update", 0),
                    "pdu_info": (
                        self.device.pdu_info.copy()
                        if hasattr(self.device, "pdu_info")
                        else {}
                    ),
                }
            else:
                _LOGGER.warning("Device is not connected, cannot update data")
                raise UpdateFailed("Device not connected")

        except UpdateFailed:
            # Re-raise UpdateFailed exceptions without modification
            raise
        except Exception as e:
            _LOGGER.error("Unexpected error updating from device: %s", e)
            # Convert exception into update failure for coordinator
            raise UpdateFailed(f"Error communicating with device: {e}")
