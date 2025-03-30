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
        self._attr_name = f"Outlet {outlet}"
        self._attr_unique_id = f"{controller.pdu_serial}_outlet_{outlet}"
        # Set available as False initially until we confirm connection
        self._attr_available = False
        self._state = None
        self._last_commanded_state = None
        self._pending_update = False
        # Initialize attribute values
        self._current = None
        self._power = None
        self._energy = None
        self._power_factor = None

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
        # Only available if controller is connected
        return self._controller.connected and self._controller.available

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the outlet on."""
        try:
            self._last_commanded_state = True
            self._pending_update = True

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
                        "Outlet %d state mismatch! Commanded: %s, Actual: %s",
                        self._outlet,
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

        # Update power attributes
        self._current = self._controller.outlet_current.get(self._outlet)
        self._power = self._controller.outlet_power.get(self._outlet)
        self._energy = self._controller.outlet_energy.get(self._outlet)
        self._power_factor = self._controller.outlet_power_factor.get(self._outlet)

        # Update name from controller if available
        outlet_name = self._controller.outlet_names.get(self._outlet)
        if outlet_name and outlet_name.strip():
            self._attr_name = outlet_name
        else:
            self._attr_name = f"Outlet {self._outlet}"

        self._attr_available = self._controller.available

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional outlet information."""
        attrs = {
            "outlet_number": self._outlet,
            "can_cycle": True,
        }

        # Add all power-related attributes that are available
        if self._power is not None:
            attrs["power"] = f"{self._power:.2f} W"
        if self._current is not None:
            attrs["current"] = f"{self._current:.2f} A"
        if self._energy is not None:
            attrs["energy"] = f"{self._energy:.2f} Wh"
        if self._power_factor is not None:
            attrs["power_factor"] = f"{self._power_factor:.2f} %"

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
