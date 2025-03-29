"""Switch platform for Middle Atlantic Racklink."""

import asyncio
import logging
from typing import Any, Optional

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

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

    # Add switches even if device is not yet available
    # They will show as unavailable until connection is established
    for outlet in range(1, 9):  # Assuming 8 outlets, adjust as needed
        switches.append(RacklinkOutlet(controller, outlet))
    switches.append(RacklinkAllOn(controller))
    switches.append(RacklinkAllOff(controller))

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
            await asyncio.wait_for(
                self._controller.set_outlet_state(self._outlet, True), timeout=10
            )
            self._state = True
            self.async_write_ha_state()
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout turning on outlet %s", self._outlet)
        except Exception as err:
            _LOGGER.error("Error turning on outlet %s: %s", self._outlet, err)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the outlet off."""
        try:
            await asyncio.wait_for(
                self._controller.set_outlet_state(self._outlet, False), timeout=10
            )
            self._state = False
            self.async_write_ha_state()
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout turning off outlet %s", self._outlet)
        except Exception as err:
            _LOGGER.error("Error turning off outlet %s: %s", self._outlet, err)

    async def async_update(self) -> None:
        """Fetch new state data for this outlet."""
        if not self._controller.connected:
            self._attr_available = False
            return

        self._state = self._controller.outlet_states.get(self._outlet, False)
        self._attr_name = self._controller.outlet_names.get(
            self._outlet, f"Outlet {self._outlet}"
        )
        self._attr_available = self._controller.available

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional outlet information."""
        attrs = {
            "outlet_number": self._outlet,
            "can_cycle": True,
        }

        # Only add power and current if they exist
        if power := self._controller.outlet_power.get(self._outlet):
            attrs["power"] = power
        if current := self._controller.outlet_current.get(self._outlet):
            attrs["current"] = current

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


class RacklinkAllOn(SwitchEntity):
    """Representation of a switch that turns all outlets on."""

    def __init__(self, controller: RacklinkController) -> None:
        """Initialize the all-on switch."""
        self._controller = controller
        self._attr_name = "All Outlets On"
        self._attr_unique_id = f"{controller.pdu_serial}_all_on"
        self._state = False
        self._attr_available = False

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
    def is_on(self) -> bool:
        """Return true if all outlets are on."""
        return self._state

    @property
    def available(self) -> bool:
        """Return if switch is available."""
        return self._controller.connected and self._controller.available

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn all outlets on."""
        try:
            await asyncio.wait_for(self._controller.set_all_outlets(True), timeout=10)
            self._state = True
            self.async_write_ha_state()
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout turning on all outlets")
        except Exception as err:
            _LOGGER.error("Error turning on all outlets: %s", err)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Reset state."""
        self._state = False
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Update availability."""
        self._attr_available = self._controller.connected and self._controller.available


class RacklinkAllOff(SwitchEntity):
    """Representation of a switch that turns all outlets off."""

    def __init__(self, controller: RacklinkController) -> None:
        """Initialize the all-off switch."""
        self._controller = controller
        self._attr_name = "All Outlets Off"
        self._attr_unique_id = f"{controller.pdu_serial}_all_off"
        self._state = False
        self._attr_available = False

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
    def is_on(self) -> bool:
        """Return true if active."""
        return self._state

    @property
    def available(self) -> bool:
        """Return if switch is available."""
        return self._controller.connected and self._controller.available

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn all outlets off."""
        try:
            await asyncio.wait_for(self._controller.set_all_outlets(False), timeout=10)
            self._state = True
            self.async_write_ha_state()
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout turning off all outlets")
        except Exception as err:
            _LOGGER.error("Error turning off all outlets: %s", err)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Reset state."""
        self._state = False
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Update availability."""
        self._attr_available = self._controller.connected and self._controller.available
