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
        switches.append(RacklinkOutlet(controller, outlet))

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


class RacklinkOutlet(SwitchEntity):
    """Representation of a Racklink outlet switch."""

    _attr_has_entity_name = True
    entity_category = None

    def __init__(self, controller: RacklinkController, outlet: int) -> None:
        """Initialize the outlet switch."""
        self._controller = controller
        self._outlet = outlet
        # Get the outlet name from the controller if available
        self._outlet_name = controller.outlet_names.get(outlet, f"Outlet {outlet}")
        self._attr_name = f"Outlet {outlet}"
        self._attr_unique_id = f"{controller.pdu_serial}_outlet_{outlet}"
        # Set available as False initially until we confirm connection
        self._attr_available = False
        self._state = controller.outlet_states.get(outlet, None)
        self._last_commanded_state = None
        self._pending_update = False

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
        return self._state

    @property
    def available(self) -> bool:
        """Return if switch is available."""
        # Only available if controller is connected and outlet exists in data
        return (
            self._controller.connected
            and self._controller.available
            and self._outlet in self._controller.outlet_states
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the outlet on."""
        try:
            self._last_commanded_state = True
            self._pending_update = True

            _LOGGER.debug("Turning on outlet %d (%s)", self._outlet, self._outlet_name)
            success = await asyncio.wait_for(
                self._controller.set_outlet_state(self._outlet, True), timeout=10
            )

            # Optimistically update state if we got a success response
            if success:
                _LOGGER.debug("Command to turn on outlet %d succeeded", self._outlet)
                self._state = True
                self.async_write_ha_state()
            else:
                _LOGGER.warning(
                    "Command to turn on outlet %d may have failed", self._outlet
                )
                # Still set state optimistically, but we'll verify
                self._state = True
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
                self._controller.set_outlet_state(self._outlet, False), timeout=10
            )

            # Optimistically update state if we got a success response
            if success:
                _LOGGER.debug("Command to turn off outlet %d succeeded", self._outlet)
                self._state = False
                self.async_write_ha_state()
            else:
                _LOGGER.warning(
                    "Command to turn off outlet %d may have failed", self._outlet
                )
                # Still set state optimistically, but we'll verify
                self._state = False
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

        _LOGGER.debug("Refreshing outlet %d state to verify command", self._outlet)
        try:
            # Force refresh of outlet states
            await asyncio.wait_for(
                self._controller.get_all_outlet_states(force_refresh=True), timeout=10
            )

            # Update our state based on the refreshed data
            actual_state = self._controller.outlet_states.get(self._outlet, None)

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
                self._state = actual_state

                # Extra attempt to sync if there's a mismatch to ensure UI matches device
                if self._state != self._last_commanded_state:
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

            # Always update our state to match the device
            self._state = actual_state
            self.async_write_ha_state()

        except Exception as err:
            _LOGGER.error("Error refreshing outlet %d state: %s", self._outlet, err)
        finally:
            self._pending_update = False

    async def async_update(self) -> None:
        """Fetch updated state from the device."""
        try:
            # Only update if not pending a command confirmation
            if self._pending_update:
                return

            # Get the current state directly from controller
            current_state = self._controller.outlet_states.get(self._outlet)
            current_name = self._controller.outlet_names.get(self._outlet)

            # Check if we have outlet state data
            if current_state is None:
                _LOGGER.debug(
                    "No state data for outlet %d, controller has data for outlets: %s",
                    self._outlet,
                    list(sorted(self._controller.outlet_states.keys())),
                )

                # Try to force an update if we don't have data
                if self._controller.connected:
                    _LOGGER.debug(
                        "Connected but missing outlet %d data - forcing update",
                        self._outlet,
                    )
                    # Force controller to update
                    await self._controller.update()

                    # Try again
                    current_state = self._controller.outlet_states.get(self._outlet)
                    current_name = self._controller.outlet_names.get(self._outlet)

                    if current_state is not None:
                        _LOGGER.debug(
                            "After forcing update, got outlet %d state: %s",
                            self._outlet,
                            current_state,
                        )
                    else:
                        _LOGGER.warning(
                            "Still no data for outlet %d after forcing update",
                            self._outlet,
                        )
                        # Try one more direct query as a last resort
                        try:
                            response = await self._controller.send_command(
                                f"show outlets {self._outlet} details"
                            )
                            if response:
                                _LOGGER.debug(
                                    "Direct outlet %d query response: %s",
                                    self._outlet,
                                    response[:100],
                                )
                                # Parse outlet state
                                state_match = re.search(
                                    r"Power state:\s*(\w+)", response, re.IGNORECASE
                                )
                                if state_match:
                                    raw_state = state_match.group(1)
                                    current_state = raw_state.lower() in [
                                        "on",
                                        "1",
                                        "true",
                                        "yes",
                                        "active",
                                    ]
                                    self._controller.outlet_states[self._outlet] = (
                                        current_state
                                    )
                                    _LOGGER.debug(
                                        "Direct query found outlet %d state: %s",
                                        self._outlet,
                                        current_state,
                                    )

                                    # Also try to get name
                                    name_match = re.search(
                                        r"Outlet \d+ - (.+?)[\r\n]", response
                                    )
                                    if name_match:
                                        current_name = name_match.group(1).strip()
                                        self._controller.outlet_names[self._outlet] = (
                                            current_name
                                        )
                        except Exception as e:
                            _LOGGER.debug("Error in direct outlet query: %s", e)

            # Update state if available (don't change state during pending update)
            if current_state is not None:
                if self._state != current_state:
                    _LOGGER.debug(
                        "Outlet %d state changed from %s to %s",
                        self._outlet,
                        self._state,
                        current_state,
                    )
                self._state = current_state

            # Update name if it's changed
            if current_name is not None:
                outlet_name = current_name
            else:
                outlet_name = f"Outlet {self._outlet}"

            if outlet_name != self._outlet_name:
                _LOGGER.debug(
                    "Outlet %d name changed from '%s' to '%s'",
                    self._outlet,
                    self._outlet_name,
                    outlet_name,
                )
                self._outlet_name = outlet_name
                self._attr_name = outlet_name

        except Exception as err:
            _LOGGER.error("Error updating outlet %d: %s", self._outlet, err)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional outlet information."""
        attrs = {
            "outlet_number": self._outlet,
            "outlet_name": self._outlet_name,
            "outlet_can_cycle": True,
        }

        # Add all available power and metrics data
        if power := self._controller.outlet_power.get(self._outlet):
            attrs["power_w"] = power
        if current := self._controller.outlet_current.get(self._outlet):
            attrs["current_a"] = current
        if energy := self._controller.outlet_energy.get(self._outlet):
            attrs["energy_wh"] = energy
        if power_factor := self._controller.outlet_power_factor.get(self._outlet):
            attrs["power_factor"] = power_factor
        if voltage := self._controller.outlet_voltage.get(self._outlet):
            attrs["voltage_v"] = voltage
        if frequency := self._controller.outlet_line_frequency.get(self._outlet):
            attrs["frequency_hz"] = frequency

        return attrs

    async def async_added_to_hass(self) -> None:
        """Register callbacks when entity is added."""
        # Listen for entity updates from the dispatcher signal
        self.async_on_remove(
            self.hass.helpers.dispatcher.async_dispatcher_connect(
                f"{DOMAIN}_entity_update", self.async_write_ha_state
            )
        )

    async def async_cycle(self) -> None:
        """Cycle the outlet power."""
        try:
            _LOGGER.debug("Cycling outlet %d (%s)", self._outlet, self._outlet_name)
            await asyncio.wait_for(
                self._controller.cycle_outlet(self._outlet), timeout=10
            )
            # Schedule a refresh to confirm state after a short delay
            async_call_later(self.hass, 5, self._async_refresh_state)
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout cycling outlet %s", self._outlet)
        except Exception as err:
            _LOGGER.error("Error cycling outlet %s: %s", self._outlet, err)

    @property
    def assumed_state(self) -> bool:
        """Return True if we do real-time state from the device."""
        return True
