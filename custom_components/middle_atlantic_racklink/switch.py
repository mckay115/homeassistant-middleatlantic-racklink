"""Switch platform for the Middle Atlantic RackLink integration."""

from __future__ import annotations

from typing import Any, Dict, Optional, Set

import logging

import voluptuous as vol

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import RacklinkConfigEntry
from .const import (
    SERVICE_CYCLE_ALL_OUTLETS,
    SERVICE_CYCLE_OUTLET,
    SERVICE_SET_OUTLET_NAME,
    SERVICE_SET_PDU_NAME,
)
from .coordinator import RacklinkCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: RacklinkConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Middle Atlantic RackLink switches from a config entry."""
    coordinator = config_entry.runtime_data

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_CYCLE_OUTLET,
        None,
        "async_cycle_outlet",
    )
    platform.async_register_entity_service(
        SERVICE_CYCLE_ALL_OUTLETS,
        None,
        "async_cycle_all_outlets",
    )
    platform.async_register_entity_service(
        SERVICE_SET_OUTLET_NAME,
        {vol.Required(CONF_NAME): cv.string},
        "async_set_outlet_name",
    )
    platform.async_register_entity_service(
        SERVICE_SET_PDU_NAME,
        {vol.Required(CONF_NAME): cv.string},
        "async_set_pdu_name",
    )

    known_outlets: Set[int] = set()

    @callback
    def _add_outlet_entities() -> None:
        """Add switches for outlets discovered on the device."""
        new_outlets = sorted(set(coordinator.outlet_data) - known_outlets)
        if not new_outlets:
            return
        known_outlets.update(new_outlets)
        async_add_entities(
            RacklinkOutletSwitch(coordinator, outlet) for outlet in new_outlets
        )

    _add_outlet_entities()
    config_entry.async_on_unload(coordinator.async_add_listener(_add_outlet_entities))


class RacklinkOutletSwitch(CoordinatorEntity[RacklinkCoordinator], SwitchEntity):
    """Representation of a Middle Atlantic RackLink outlet switch."""

    _attr_has_entity_name = True
    _attr_translation_key = "outlet"

    def __init__(self, coordinator: RacklinkCoordinator, outlet_number: int) -> None:
        """Initialize the outlet switch."""
        super().__init__(coordinator)
        self._outlet_number = outlet_number
        self._attr_unique_id = (
            f"{coordinator.controller.pdu_serial}_outlet_{outlet_number}"
        )
        self._attr_translation_placeholders = {"outlet_number": str(outlet_number)}

    @property
    def _outlet_data(self) -> Dict[str, Any]:
        """Return the coordinator data for this outlet."""
        return self.coordinator.outlet_data.get(self._outlet_number, {})

    @property
    def is_on(self) -> Optional[bool]:
        """Return True if the outlet is on."""
        return self._outlet_data.get("state")

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return outlet attributes reported by the device."""
        attributes: Dict[str, Any] = {"outlet_number": self._outlet_number}
        outlet_data = self._outlet_data
        if outlet_data.get("name"):
            attributes["outlet_name"] = outlet_data["name"]
        if outlet_data.get("attrs"):
            attributes.update(outlet_data["attrs"])
        return attributes

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return self.coordinator.device_info

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return (
            super().available
            and self.coordinator.controller.connected
            and self._outlet_number in self.coordinator.outlet_data
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the outlet on."""
        await self.coordinator.turn_outlet_on(self._outlet_number)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the outlet off."""
        await self.coordinator.turn_outlet_off(self._outlet_number)

    async def async_cycle_outlet(self) -> None:
        """Cycle this outlet (service handler)."""
        await self.coordinator.cycle_outlet(self._outlet_number)

    async def async_cycle_all_outlets(self) -> None:
        """Cycle all outlets on the PDU (service handler)."""
        await self.coordinator.cycle_all_outlets()

    async def async_set_outlet_name(self, name: str) -> None:
        """Set the label of this outlet on the device (service handler)."""
        await self.coordinator.set_outlet_name(self._outlet_number, name)

    async def async_set_pdu_name(self, name: str) -> None:
        """Set the PDU display name on the device (service handler)."""
        await self.coordinator.set_pdu_name(name)
