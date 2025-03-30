"""Switch platform for the Middle Atlantic RackLink integration."""

from __future__ import annotations
import logging
from typing import Any, Callable, Optional

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

    # Add outlets as switches
    for outlet_num in coordinator.outlet_data:
        entities.append(RacklinkOutletSwitch(coordinator, outlet_num))

    async_add_entities(entities)


class RacklinkOutletSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of a Middle Atlantic RackLink outlet switch."""

    def __init__(self, coordinator: RacklinkCoordinator, outlet_number: int) -> None:
        """Initialize the outlet switch."""
        super().__init__(coordinator)
        self._outlet_number = outlet_number

        # Set unique ID and name
        self._attr_unique_id = (
            f"{coordinator.controller.pdu_serial}_outlet_{outlet_number}"
        )
        self._name = f"Outlet {outlet_number}"

    @property
    def name(self) -> str:
        """Return the name of the switch."""
        outlet_data = self.coordinator.outlet_data.get(self._outlet_number, {})
        custom_name = outlet_data.get("name")
        if custom_name and custom_name != f"Outlet {self._outlet_number}":
            return custom_name
        return self._name

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
        return self.coordinator.available

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the outlet on."""
        await self.coordinator.turn_outlet_on(self._outlet_number)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the outlet off."""
        await self.coordinator.turn_outlet_off(self._outlet_number)
