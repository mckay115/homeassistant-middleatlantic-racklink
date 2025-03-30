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
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_MANUFACTURER, ATTR_MODEL, DOMAIN
from .controller import RacklinkController
from .coordinator import RacklinkCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Middle Atlantic Racklink switches from config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    controller = data["controller"]
    coordinator = data["coordinator"]
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
        switches.append(RacklinkOutlet(coordinator, outlet))

    async_add_entities(switches)


class RacklinkOutlet(CoordinatorEntity, SwitchEntity):
    """Representation of a Racklink outlet switch."""

    def __init__(self, coordinator: RacklinkCoordinator, outlet: int):
        """Initialize the outlet switch."""
        super().__init__(coordinator)
        self._outlet = outlet
        self._controller = coordinator.controller

        # Set entity attributes
        self._attr_unique_id = f"{self._controller.pdu_serial}_outlet_{outlet}"
        self._attr_has_entity_name = True
        self._attr_name = f"Outlet {outlet}"

        _LOGGER.debug(
            "Initialized outlet switch for outlet %s on PDU %s",
            outlet,
            self._controller.pdu_name,
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        device_info = {
            "identifiers": {(DOMAIN, self._controller.pdu_serial)},
            "name": f"Racklink PDU {self._controller.pdu_name}",
            "manufacturer": ATTR_MANUFACTURER,
            "model": self._controller.pdu_model or ATTR_MODEL,
            "sw_version": self._controller.pdu_firmware,
        }

        # Add MAC address as a connection info if available
        if self._controller.mac_address:
            device_info["connections"] = {("mac", self._controller.mac_address)}

        return device_info

    @property
    def is_on(self) -> Optional[bool]:
        """Return true if outlet is on."""
        if self.coordinator.data and "outlets" in self.coordinator.data:
            # Get state from coordinator data
            outlets = self.coordinator.data.get("outlets", {})
            if self._outlet in outlets:
                return outlets[self._outlet]["state"]
        # Fall back to controller state if coordinator data is not available
        return self._controller.outlet_states.get(self._outlet, None)

    @property
    def available(self) -> bool:
        """Return if switch is available."""
        # Check if data is available in the coordinator
        if not self.coordinator.last_update_success:
            return False

        if self.coordinator.data and "outlets" in self.coordinator.data:
            return self._outlet in self.coordinator.data["outlets"]

        # As fallback, check if the controller has state for this outlet
        return (
            self._controller.connected
            and self._controller.available
            and self._outlet in self._controller.outlet_states
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the outlet on."""
        _LOGGER.debug("Turning outlet %s ON", self._outlet)

        try:
            # Use coordinator to turn on outlet (which handles refreshing state)
            success = await self.coordinator.turn_outlet_on(self._outlet)

            if not success:
                _LOGGER.warning("Failed to turn outlet %s ON", self._outlet)
        except Exception as e:
            _LOGGER.error("Error turning outlet %s ON: %s", self._outlet, e)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the outlet off."""
        _LOGGER.debug("Turning outlet %s OFF", self._outlet)

        try:
            # Use coordinator to turn off outlet (which handles refreshing state)
            success = await self.coordinator.turn_outlet_off(self._outlet)

            if not success:
                _LOGGER.warning("Failed to turn outlet %s OFF", self._outlet)
        except Exception as e:
            _LOGGER.error("Error turning outlet %s OFF: %s", self._outlet, e)

    async def async_cycle(self) -> None:
        """Cycle the outlet power."""
        _LOGGER.debug("Cycling outlet %s", self._outlet)

        try:
            success = await self.coordinator.cycle_outlet(self._outlet)

            if not success:
                _LOGGER.warning("Failed to cycle outlet %s", self._outlet)
        except Exception as e:
            _LOGGER.error("Error cycling outlet %s: %s", self._outlet, e)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional outlet information."""
        attrs = {
            "outlet_number": self._outlet,
            "can_cycle": True,
        }

        # Get outlet data from coordinator
        if self.coordinator.data and "outlets" in self.coordinator.data:
            outlet_data = self.coordinator.data["outlets"].get(self._outlet, {})

            # Add all available power and metrics data
            if "power" in outlet_data and outlet_data["power"] is not None:
                attrs["power"] = f"{outlet_data['power']:.1f} W"

            if "current" in outlet_data and outlet_data["current"] is not None:
                attrs["current"] = f"{outlet_data['current']:.2f} A"

            if "voltage" in outlet_data and outlet_data["voltage"] is not None:
                attrs["voltage"] = f"{outlet_data['voltage']:.1f} V"

            if "energy" in outlet_data and outlet_data["energy"] is not None:
                attrs["energy"] = f"{outlet_data['energy']:.1f} Wh"

            if (
                "power_factor" in outlet_data
                and outlet_data["power_factor"] is not None
            ):
                attrs["power_factor"] = f"{outlet_data['power_factor']:.2f}"

            # Add name if available
            if "name" in outlet_data and outlet_data["name"]:
                outlet_name = outlet_data["name"]
                if not outlet_name.startswith(f"Outlet {self._outlet}"):
                    attrs["name"] = outlet_name
                    # Also update the entity name
                    self._attr_name = f"Outlet {self._outlet} - {outlet_name}"

        return attrs
