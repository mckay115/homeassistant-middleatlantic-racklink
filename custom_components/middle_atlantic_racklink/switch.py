"""Switch platform for the Middle Atlantic RackLink integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN
from .coordinator import RacklinkCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Middle Atlantic RackLink switches from config entry."""
    coordinator: RacklinkCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities = []

    # Add outlets as switches - ensure we create switches even if no outlet data yet
    # Default to creating 8 outlets, which is common for these PDUs
    max_outlets = 8
    existing_outlets = list(coordinator.outlet_data.keys())

    # Use existing outlet data if available, otherwise create for standard number
    outlet_numbers = existing_outlets if existing_outlets else range(1, max_outlets + 1)

    for outlet_num in outlet_numbers:
        entities.append(RacklinkOutletSwitch(coordinator, outlet_num))

    async_add_entities(entities)


class RacklinkOutletSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of a Middle Atlantic RackLink outlet switch."""

    def __init__(self, coordinator: RacklinkCoordinator, outlet_number: int) -> None:
        """Initialize the outlet switch."""
        super().__init__(coordinator)
        self._outlet_number = outlet_number

        # Set unique ID
        self._attr_unique_id = (
            f"{coordinator.controller.pdu_serial}_outlet_{outlet_number}"
        )

        # Always include outlet number in name, custom name will be added in @name property
        self._base_name = f"Outlet {outlet_number}"

    @property
    def name(self) -> str:
        """Return the name of the switch, always including outlet number."""
        outlet_data = self.coordinator.outlet_data.get(self._outlet_number, {})
        custom_name = outlet_data.get("name")

        # If we have a custom name that's different from the default, use it with the outlet number
        if custom_name and custom_name != self._base_name:
            return f"{self._base_name}: {custom_name}"

        # Otherwise just return the base name (Outlet X)
        return self._base_name

    @property
    def is_on(self) -> bool:
        """Return True if the outlet is on."""
        outlet_data = self.coordinator.outlet_data.get(self._outlet_number, {})
        return outlet_data.get("state", False)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return self.coordinator.device_info

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        # Consider the switch available if coordinator is available,
        # even if no outlet data yet (will show as OFF)
        return self.coordinator.available

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the outlet on."""
        await self.coordinator.turn_outlet_on(self._outlet_number)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the outlet off."""
        await self.coordinator.turn_outlet_off(self._outlet_number)

    def turn_on(self, **kwargs: Any) -> None:
        """Turn the outlet on (abstract method implementation)."""
        raise NotImplementedError("Use async_turn_on instead")

    def turn_off(self, **kwargs: Any) -> None:
        """Turn the outlet off (abstract method implementation)."""
        raise NotImplementedError("Use async_turn_off instead")
