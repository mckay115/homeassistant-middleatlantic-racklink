"""Switch platform for Middle Atlantic Racklink."""

import asyncio
import logging
from typing import Any, Optional

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_MANUFACTURER, ATTR_MODEL, DOMAIN
from .device import RacklinkDevice
from .coordinator import RacklinkDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Middle Atlantic Racklink switches from config entry."""
    device = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = hass.data[DOMAIN][config_entry.entry_id + "_coordinator"]
    switches = []

    # Get model capabilities to determine number of outlets
    capabilities = device.get_model_capabilities()
    outlet_count = capabilities.get("num_outlets", 8)  # Default to 8 if not determined

    _LOGGER.info(
        "Setting up %d outlet switches for %s (%s)",
        outlet_count,
        device.pdu_name,
        device.pdu_model,
    )

    # Add switches even if device is not yet available
    # They will show as unavailable until connection is established
    for outlet in range(1, outlet_count + 1):
        switches.append(RacklinkOutlet(device, coordinator, outlet))

    async_add_entities(switches)


class RacklinkOutlet(CoordinatorEntity, SwitchEntity):
    """Representation of a Racklink outlet switch."""

    def __init__(
        self,
        device: RacklinkDevice,
        coordinator: RacklinkDataUpdateCoordinator,
        outlet: int,
    ) -> None:
        """Initialize the outlet switch."""
        super().__init__(coordinator)
        self._device = device
        self._outlet = outlet
        self._last_commanded_state = None
        self._pending_update = False

        # Get the outlet name from the device if available
        # We'll update this in the coordinator data
        self._outlet_name = device.outlet_names.get(outlet, f"Outlet {outlet}")
        self._attr_unique_id = f"{device.pdu_serial}_outlet_{outlet}"
        self._attr_name = self._outlet_name

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this entity."""
        return {
            "identifiers": {(DOMAIN, self._device.pdu_serial)},
            "name": f"Racklink PDU {self._device.pdu_name}",
            "manufacturer": ATTR_MANUFACTURER,
            "model": self._device.pdu_model or ATTR_MODEL,
            "sw_version": self._device.pdu_firmware,
        }

    @property
    def is_on(self) -> Optional[bool]:
        """Return true if outlet is on."""
        if self.coordinator.data:
            # Get state from coordinator data
            outlet_states = self.coordinator.data.get("outlet_states", {})
            return outlet_states.get(self._outlet, None)
        # Fall back to device state if coordinator data is not available
        return self._device.outlet_states.get(self._outlet, None)

    @property
    def available(self) -> bool:
        """Return if switch is available."""
        # Use device connection status to determine availability
        coordinator_has_data = (
            self.coordinator.data
            and self._outlet in self.coordinator.data.get("outlet_states", {})
        )
        device_has_data = self._outlet in self._device.outlet_states

        return (
            self._device.connected
            and self._device.available
            and (coordinator_has_data or device_has_data)
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the outlet on."""
        try:
            self._last_commanded_state = True
            self._pending_update = True

            _LOGGER.debug("Turning on outlet %d (%s)", self._outlet, self._outlet_name)
            success = await asyncio.wait_for(
                self._device.set_outlet_state(self._outlet, True), timeout=10
            )

            # Optimistically update state if we got a success response
            if success:
                _LOGGER.debug("Command to turn on outlet %d succeeded", self._outlet)
                self._device.outlet_states[self._outlet] = True
                self.async_write_ha_state()
            else:
                _LOGGER.warning(
                    "Command to turn on outlet %d may have failed", self._outlet
                )
                # Still set state optimistically, but we'll verify
                self._device.outlet_states[self._outlet] = True
                self.async_write_ha_state()

            # Schedule a refresh to confirm state after a short delay
            async_call_later(self.hass, 3, self._async_refresh_state)

        except asyncio.TimeoutError:
            _LOGGER.error("Timeout turning on outlet %s", self._outlet)
            self._pending_update = False
        except Exception as err:
            _LOGGER.error("Error turning on outlet %s: %s", self._outlet, err)
            self._pending_update = False

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the outlet off."""
        try:
            self._last_commanded_state = False
            self._pending_update = True

            _LOGGER.debug("Turning off outlet %d (%s)", self._outlet, self._outlet_name)
            success = await asyncio.wait_for(
                self._device.set_outlet_state(self._outlet, False), timeout=10
            )

            # Optimistically update state if we got a success response
            if success:
                _LOGGER.debug("Command to turn off outlet %d succeeded", self._outlet)
                self._device.outlet_states[self._outlet] = False
                self.async_write_ha_state()
            else:
                _LOGGER.warning(
                    "Command to turn off outlet %d may have failed", self._outlet
                )
                # Still set state optimistically, but we'll verify
                self._device.outlet_states[self._outlet] = False
                self.async_write_ha_state()

            # Schedule a refresh to confirm state after a short delay
            async_call_later(self.hass, 3, self._async_refresh_state)

        except asyncio.TimeoutError:
            _LOGGER.error("Timeout turning off outlet %s", self._outlet)
            self._pending_update = False
        except Exception as err:
            _LOGGER.error("Error turning off outlet %s: %s", self._outlet, err)
            self._pending_update = False

    async def _async_refresh_state(self, _now=None):
        """Refresh the outlet state to verify the command took effect."""
        if not self._pending_update:
            return

        try:
            # Wait a little longer if needed for some devices that respond more slowly
            await self.coordinator.async_request_refresh()

            # Check if we have data after refresh
            if self.coordinator.data and "outlet_states" in self.coordinator.data:
                outlet_states = self.coordinator.data.get("outlet_states", {})

                # Check that the state actually changed as expected
                current_state = outlet_states.get(self._outlet)

                if (
                    current_state is not None
                    and current_state != self._last_commanded_state
                ):
                    _LOGGER.warning(
                        "Outlet %d state didn't match expected state after command: expected=%s, actual=%s",
                        self._outlet,
                        self._last_commanded_state,
                        current_state,
                    )
                else:
                    _LOGGER.debug(
                        "Confirmed outlet %d state is now %s",
                        self._outlet,
                        "ON" if current_state else "OFF",
                    )
        except Exception as err:
            _LOGGER.error("Error refreshing outlet %s state: %s", self._outlet, err)
        finally:
            self._pending_update = False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes of the outlet."""
        attrs = {"outlet_number": self._outlet, "pdu_name": self._device.pdu_name}

        # Add power metrics if available
        if self.coordinator.data:
            # Current (A)
            if (
                "outlet_current" in self.coordinator.data
                and self._outlet in self.coordinator.data["outlet_current"]
            ):
                attrs["current"] = self.coordinator.data["outlet_current"][self._outlet]

            # Voltage (V)
            if (
                "outlet_voltage" in self.coordinator.data
                and self._outlet in self.coordinator.data["outlet_voltage"]
            ):
                attrs["voltage"] = self.coordinator.data["outlet_voltage"][self._outlet]

            # Power (W)
            if (
                "outlet_power" in self.coordinator.data
                and self._outlet in self.coordinator.data["outlet_power"]
            ):
                attrs["power"] = self.coordinator.data["outlet_power"][self._outlet]

            # Energy (kWh)
            if (
                "outlet_energy" in self.coordinator.data
                and self._outlet in self.coordinator.data["outlet_energy"]
            ):
                attrs["energy"] = self.coordinator.data["outlet_energy"][self._outlet]

            # Power Factor
            if (
                "outlet_power_factor" in self.coordinator.data
                and self._outlet in self.coordinator.data["outlet_power_factor"]
            ):
                attrs["power_factor"] = self.coordinator.data["outlet_power_factor"][
                    self._outlet
                ]

        return attrs

    async def async_cycle(self) -> None:
        """Cycle the outlet (turn off, then back on after a delay)."""
        try:
            # Indicate pending update
            self._pending_update = True

            # Use the device's cycle_outlet method
            _LOGGER.debug("Cycling outlet %d (%s)", self._outlet, self._outlet_name)
            success = await asyncio.wait_for(
                self._device.cycle_outlet(self._outlet), timeout=15
            )

            if success:
                _LOGGER.debug(
                    "Successfully initiated cycle for outlet %d", self._outlet
                )
                # Immediate state update to show outlet is now off
                # (cycle turns off then on after a delay)
                self._device.outlet_states[self._outlet] = False
                self.async_write_ha_state()

                # Schedule a refresh to update state after cycling should complete
                # Most devices take 5-10 seconds to complete a cycle
                async_call_later(self.hass, 12, self._async_refresh_state)
            else:
                _LOGGER.warning("Failed to cycle outlet %d", self._outlet)
                self._pending_update = False

        except asyncio.TimeoutError:
            _LOGGER.error("Timeout cycling outlet %s", self._outlet)
            self._pending_update = False
        except Exception as err:
            _LOGGER.error("Error cycling outlet %s: %s", self._outlet, err)
            self._pending_update = False
