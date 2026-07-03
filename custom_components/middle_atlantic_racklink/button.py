"""Button platform for the Middle Atlantic RackLink integration."""

from __future__ import annotations

from . import RacklinkConfigEntry
from .coordinator import RacklinkCoordinator
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import callback, HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from typing import Any, Set

import logging

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class RacklinkButtonEntityDescription(ButtonEntityDescription):
    """Describes a RackLink button that invokes a coordinator action."""

    press_fn: Callable[[RacklinkCoordinator], Coroutine[Any, Any, None]]


PDU_BUTTONS: tuple[RacklinkButtonEntityDescription, ...] = (
    RacklinkButtonEntityDescription(
        key="all_outlets_cycle",
        translation_key="cycle_all_outlets",
        press_fn=lambda coordinator: coordinator.cycle_all_outlets(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: RacklinkConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Middle Atlantic RackLink buttons from a config entry."""
    coordinator = config_entry.runtime_data

    entities: list[ButtonEntity] = [
        RacklinkPduButton(coordinator, description) for description in PDU_BUTTONS
    ]
    async_add_entities(entities)

    known_outlets: Set[int] = set()

    @callback
    def _add_outlet_entities() -> None:
        """Add cycle buttons for outlets discovered on the device."""
        new_outlets = sorted(set(coordinator.outlet_data) - known_outlets)
        if not new_outlets:
            return
        known_outlets.update(new_outlets)
        async_add_entities(
            RacklinkOutletCycleButton(coordinator, outlet) for outlet in new_outlets
        )

    _add_outlet_entities()
    config_entry.async_on_unload(coordinator.async_add_listener(_add_outlet_entities))


class RacklinkButtonBase(CoordinatorEntity[RacklinkCoordinator], ButtonEntity):
    """Base class for Middle Atlantic RackLink button entities."""

    _attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return self.coordinator.device_info

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return super().available and self.coordinator.controller.connected


class RacklinkPduButton(RacklinkButtonBase):
    """Button invoking a PDU-wide coordinator action."""

    entity_description: RacklinkButtonEntityDescription

    def __init__(
        self,
        coordinator: RacklinkCoordinator,
        description: RacklinkButtonEntityDescription,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.controller.pdu_serial}_{description.key}"

    async def async_press(self) -> None:
        """Press the button."""
        await self.entity_description.press_fn(self.coordinator)


class RacklinkOutletCycleButton(RacklinkButtonBase):
    """Button to cycle power for a specific outlet."""

    _attr_translation_key = "cycle_outlet"

    def __init__(self, coordinator: RacklinkCoordinator, outlet_number: int) -> None:
        """Initialize the outlet cycle button."""
        super().__init__(coordinator)
        self._outlet_number = outlet_number
        self._attr_unique_id = (
            f"{coordinator.controller.pdu_serial}_outlet_{outlet_number}_cycle"
        )
        self._attr_translation_placeholders = {"outlet_number": str(outlet_number)}

    async def async_press(self) -> None:
        """Press the button."""
        await self.coordinator.cycle_outlet(self._outlet_number)
