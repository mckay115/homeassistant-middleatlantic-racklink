"""Switch platform for Middle Atlantic Racklink."""

import asyncio
import logging
import re
from typing import Any, Optional

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import ATTR_MANUFACTURER, ATTR_MODEL, DOMAIN
from .racklink_controller import RacklinkController

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Middle Atlantic Racklink switches from config entry."""
    controller = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = hass.data[DOMAIN][config_entry.entry_id + "_coordinator"]
    switches = []

    # Get model capabilities to determine number of outlets
    capabilities = controller.get_model_capabilities()
    outlet_count = capabilities.get("num_outlets", 8)  # Default to 8 if not determined

    _LOGGER.info(
        "Setting up %d outlet switches for %s (%s)",
        outlet_count,
        controller.pdu_name,
        controller.pdu_model,
    )

    # Add switches even if device is not yet available
    # They will show as unavailable until connection is established
    for outlet in range(1, outlet_count + 1):
        switches.append(RacklinkOutlet(controller, coordinator, outlet))

    async_add_entities(switches)

    async def cycle_outlet(call: ServiceCall) -> None:
        """Cycle power for a specific outlet."""
        outlet = call.data.get("outlet")
        if not outlet:
            _LOGGER.error("Outlet number is required for cycle_outlet service")
            return

        _LOGGER.info("Cycling outlet %s on %s", outlet, controller.pdu_name)
        try:
            await asyncio.wait_for(controller.cycle_outlet(outlet), timeout=10)
        except asyncio.TimeoutError:
            _LOGGER.error("Outlet cycle operation timed out")
        except Exception as err:
            _LOGGER.error("Error cycling outlet %s: %s", outlet, err)

    hass.services.async_register(DOMAIN, "cycle_outlet", cycle_outlet)


class RacklinkOutlet(CoordinatorEntity, SwitchEntity):
    """Representation of a Racklink outlet switch."""

    def __init__(
        self,
        controller: RacklinkController,
        coordinator: DataUpdateCoordinator,
        outlet: int,
    ) -> None:
        """Initialize the outlet switch."""
        super().__init__(coordinator)
        self._controller = controller
        self._outlet = outlet
        self._last_commanded_state = None
        self._pending_update = False

        # Get the outlet name from the controller if available
        # We'll update this in the coordinator data
        self._outlet_name = controller.outlet_names.get(outlet, f"Outlet {outlet}")
        self._attr_unique_id = f"{controller.pdu_serial}_outlet_{outlet}"
        self._attr_name = self._outlet_name

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this entity."""
        return {
            "identifiers": {(DOMAIN, self._controller.pdu_serial)},
            "name": f"Racklink PDU {self._controller.pdu_name}",
            "manufacturer": ATTR_MANUFACTURER,
            "model": self._controller.pdu_model or ATTR_MODEL,
            "sw_version": self._controller.pdu_firmware,
        }

    @property
    def is_on(self) -> Optional[bool]:
        """Return true if outlet is on."""
        if self.coordinator.data:
            # Get state from coordinator data
            outlet_states = self.coordinator.data.get("outlet_states", {})
            return outlet_states.get(self._outlet, None)
        # Fall back to controller state if coordinator data is not available
        return self._controller.outlet_states.get(self._outlet, None)

    @property
    def available(self) -> bool:
        """Return if switch is available."""
        # Use controller connection status to determine availability
        coordinator_has_data = (
            self.coordinator.data
            and self._outlet in self.coordinator.data.get("outlet_states", {})
        )
        controller_has_data = self._outlet in self._controller.outlet_states

        return (
            self._controller.connected
            and self._controller.available
            and (coordinator_has_data or controller_has_data)
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the outlet on."""
        command = f"power outlets {self._outlet} on /y"
        _LOGGER.debug("Turning outlet %s ON", self._outlet)

        # Send power on command
        await self._controller.send_command(command)

        # Wait briefly for device to process the command
        await asyncio.sleep(2)

        # Verify the state change
        verification_command = f"show outlets {self._outlet} details"
        response = await self._controller.send_command(verification_command)

        # Parse the response to check if the outlet is actually on
        state_match = re.search(r"Power state:\s*(\w+)", response, re.IGNORECASE)
        if state_match:
            actual_state = state_match.group(1).lower()
            if actual_state == "on":
                _LOGGER.debug("Successfully verified outlet %s is ON", self._outlet)
                # Update internal state
                self._controller.outlet_states[self._outlet] = True
            else:
                _LOGGER.warning(
                    "Failed to turn outlet %s ON (state is %s)",
                    self._outlet,
                    actual_state,
                )
        else:
            _LOGGER.warning(
                "Could not verify outlet %s state after turn ON", self._outlet
            )

        # Trigger state update
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the outlet off."""
        command = f"power outlets {self._outlet} off /y"
        _LOGGER.debug("Turning outlet %s OFF", self._outlet)

        # Send power off command
        await self._controller.send_command(command)

        # Wait briefly for device to process the command
        await asyncio.sleep(2)

        # Verify the state change
        verification_command = f"show outlets {self._outlet} details"
        response = await self._controller.send_command(verification_command)

        # Parse the response to check if the outlet is actually off
        state_match = re.search(r"Power state:\s*(\w+)", response, re.IGNORECASE)
        if state_match:
            actual_state = state_match.group(1).lower()
            if actual_state == "off":
                _LOGGER.debug("Successfully verified outlet %s is OFF", self._outlet)
                # Update internal state
                self._controller.outlet_states[self._outlet] = False
            else:
                _LOGGER.warning(
                    "Failed to turn outlet %s OFF (state is %s)",
                    self._outlet,
                    actual_state,
                )
        else:
            _LOGGER.warning(
                "Could not verify outlet %s state after turn OFF", self._outlet
            )

        # Trigger state update
        self.async_write_ha_state()

    async def _async_refresh_state(self, _now=None):
        """Refresh the outlet state to verify the command took effect."""
        if not self._pending_update:
            return

        _LOGGER.debug("Refreshing outlet %d state to verify command", self._outlet)
        try:
            # Force refresh of outlet states through the coordinator
            await self.coordinator.async_request_refresh()

            # Update our state based on the refreshed data
            if self.coordinator.data and "outlet_states" in self.coordinator.data:
                actual_state = self.coordinator.data["outlet_states"].get(self._outlet)
            else:
                # Force refresh directly from the controller as fallback
                await self._controller.get_all_outlet_states(force_refresh=True)
                actual_state = self._controller.outlet_states.get(self._outlet)

            if actual_state is None:
                _LOGGER.warning(
                    "Refresh could not determine outlet %d state - device did not return data",
                    self._outlet,
                )
                return

            # Compare with what we expect
            if actual_state != self._last_commanded_state:
                _LOGGER.warning(
                    "Outlet %d (%s) state mismatch! Commanded: %s, Actual: %s - applying actual state",
                    self._outlet,
                    self._outlet_name,
                    self._last_commanded_state,
                    actual_state,
                )

                # The actual device state is the source of truth
                self._controller.outlet_states[self._outlet] = actual_state

                # Extra attempt to sync if there's a mismatch to ensure UI matches device
                if actual_state != self._last_commanded_state:
                    # Schedule another command to sync with actual device state
                    _LOGGER.debug(
                        "Scheduling state correction for outlet %d to match device state: %s",
                        self._outlet,
                        actual_state,
                    )
                    # We don't await this - just queue it to run after this method completes
                    asyncio.create_task(
                        self._controller.set_outlet_state(self._outlet, actual_state)
                    )
            else:
                _LOGGER.debug(
                    "Outlet %d state verified: %s",
                    self._outlet,
                    "ON" if actual_state else "OFF",
                )

            # Always update to trigger a state refresh
            self.async_write_ha_state()

        except Exception as err:
            _LOGGER.error("Error refreshing outlet %d state: %s", self._outlet, err)
        finally:
            self._pending_update = False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional outlet information."""
        attrs = {
            "outlet_number": self._outlet,
            "can_cycle": True,
        }

        # Try to get data from coordinator first
        if self.coordinator.data:
            # Add all available power and metrics data from coordinator
            if (
                "outlet_power" in self.coordinator.data
                and self._outlet in self.coordinator.data["outlet_power"]
            ):
                power = self.coordinator.data["outlet_power"].get(self._outlet)
                if power is not None:
                    attrs["power"] = f"{power:.1f} W"

            if (
                "outlet_current" in self.coordinator.data
                and self._outlet in self.coordinator.data["outlet_current"]
            ):
                current = self.coordinator.data["outlet_current"].get(self._outlet)
                if current is not None:
                    attrs["current"] = f"{current:.2f} A"

            if (
                "outlet_energy" in self.coordinator.data
                and self._outlet in self.coordinator.data["outlet_energy"]
            ):
                energy = self.coordinator.data["outlet_energy"].get(self._outlet)
                if energy is not None:
                    attrs["energy"] = f"{energy:.1f} Wh"

            if (
                "outlet_power_factor" in self.coordinator.data
                and self._outlet in self.coordinator.data["outlet_power_factor"]
            ):
                power_factor = self.coordinator.data["outlet_power_factor"].get(
                    self._outlet
                )
                if power_factor is not None:
                    attrs["power_factor"] = f"{power_factor:.2f}"

            if (
                "outlet_voltage" in self.coordinator.data
                and self._outlet in self.coordinator.data["outlet_voltage"]
            ):
                voltage = self.coordinator.data["outlet_voltage"].get(self._outlet)
                if voltage is not None:
                    attrs["voltage"] = f"{voltage:.1f} V"

            if (
                "outlet_apparent_power" in self.coordinator.data
                and self._outlet in self.coordinator.data["outlet_apparent_power"]
            ):
                apparent_power = self.coordinator.data["outlet_apparent_power"].get(
                    self._outlet
                )
                if apparent_power is not None:
                    attrs["apparent_power"] = f"{apparent_power:.1f} VA"

            if (
                "outlet_line_frequency" in self.coordinator.data
                and self._outlet in self.coordinator.data["outlet_line_frequency"]
            ):
                frequency = self.coordinator.data["outlet_line_frequency"].get(
                    self._outlet
                )
                if frequency is not None:
                    attrs["frequency"] = f"{frequency:.1f} Hz"
        else:
            # Fall back to controller data if coordinator data is not available
            if power := self._controller.outlet_power.get(self._outlet):
                attrs["power"] = f"{power:.1f} W"
            if current := self._controller.outlet_current.get(self._outlet):
                attrs["current"] = f"{current:.2f} A"
            if energy := self._controller.outlet_energy.get(self._outlet):
                attrs["energy"] = f"{energy:.1f} Wh"
            if power_factor := self._controller.outlet_power_factor.get(self._outlet):
                attrs["power_factor"] = f"{power_factor:.2f}"
            if voltage := self._controller.outlet_voltage.get(self._outlet):
                attrs["voltage"] = f"{voltage:.1f} V"
            if apparent_power := self._controller.outlet_apparent_power.get(
                self._outlet
            ):
                attrs["apparent_power"] = f"{apparent_power:.1f} VA"
            if frequency := self._controller.outlet_line_frequency.get(self._outlet):
                attrs["frequency"] = f"{frequency:.1f} Hz"

        return attrs

    async def async_cycle(self) -> None:
        """Cycle the outlet power."""
        try:
            _LOGGER.debug("Cycling outlet %d", self._outlet)
            await asyncio.wait_for(
                self._controller.cycle_outlet(self._outlet), timeout=10
            )
            # Refresh coordinator data after cycling
            await self.coordinator.async_request_refresh()
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout cycling outlet %s", self._outlet)
        except Exception as err:
            _LOGGER.error("Error cycling outlet %s: %s", self._outlet, err)
