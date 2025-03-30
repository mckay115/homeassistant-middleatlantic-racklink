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

    def __init__(self, controller: RacklinkController, outlet: int) -> None:
        """Initialize the outlet switch."""
        self._controller = controller
        self._outlet = outlet
        # Get the outlet name from the controller if available
        self._outlet_name = controller.outlet_names.get(outlet, f"Outlet {outlet}")
        self._attr_name = self._outlet_name
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
            await asyncio.wait_for(
                self._controller.set_outlet_state(self._outlet, True), timeout=10
            )

            # Optimistically update state
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
            await asyncio.wait_for(
                self._controller.set_outlet_state(self._outlet, False), timeout=10
            )

            # Optimistically update state
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
            if actual_state is not None:
                if actual_state != self._last_commanded_state:
                    _LOGGER.warning(
                        "Outlet %d (%s) state mismatch! Commanded: %s, Actual: %s",
                        self._outlet,
                        self._outlet_name,
                        self._last_commanded_state,
                        actual_state,
                    )

                self._state = actual_state
                self.async_write_ha_state()

        except Exception as err:
            _LOGGER.error("Error refreshing outlet %d state: %s", self._outlet, err)
        finally:
            self._pending_update = False

    async def async_update(self) -> None:
        """Fetch new state data for this outlet."""
        if not self._controller.connected:
            self._attr_available = False
            return

        # Skip update if we're waiting for a command to complete
        if self._pending_update:
            return

        # Update state from controller's cached data
        self._state = self._controller.outlet_states.get(self._outlet, False)

        # Update name from controller if available
        new_outlet_name = self._controller.outlet_names.get(self._outlet)
        if (
            new_outlet_name
            and new_outlet_name.strip()
            and new_outlet_name != self._outlet_name
        ):
            self._outlet_name = new_outlet_name
            self._attr_name = new_outlet_name
        elif not self._attr_name:
            self._attr_name = f"Outlet {self._outlet}"

        # Determine availability based on connection status and if outlet exists in data
        self._attr_available = (
            self._controller.connected
            and self._controller.available
            and self._outlet in self._controller.outlet_states
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional outlet information."""
        attrs = {
            "outlet_number": self._outlet,
            "can_cycle": True,
        }

        # Add all available power and metrics data
        if power := self._controller.outlet_power.get(self._outlet):
            attrs["power"] = f"{power:.1f} W"
        if current := self._controller.outlet_current.get(self._outlet):
            attrs["current"] = f"{current:.2f} A"
        if energy := self._controller.outlet_energy.get(self._outlet):
            attrs["energy"] = f"{energy:.1f} Wh"
        if power_factor := self._controller.outlet_power_factor.get(self._outlet):
            attrs["power_factor"] = f"{power_factor:.2f}"

        return attrs

    async def async_cycle(self) -> None:
        """Cycle the outlet power."""
        try:
            await asyncio.wait_for(
                self._controller.cycle_outlet(self._outlet), timeout=10
            )
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout cycling outlet %s", self._outlet)
        except Exception as err:
            _LOGGER.error("Error cycling outlet %s: %s", self._outlet, err)
