"""Button platform for the Middle Atlantic RackLink integration."""

from __future__ import annotations
import logging
from typing import Any, Callable

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
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
    """Set up the Middle Atlantic RackLink buttons from config entry."""
    coordinator: RacklinkCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities = []

    # Add outlet cycle buttons
    for outlet_num in coordinator.outlet_data:
        entities.append(RacklinkOutletCycleButton(coordinator, outlet_num))

    # Add system-wide buttons
    entities.extend(
        [
            RacklinkAllOutletsCycleButton(coordinator),
            RacklinkStartLoadSheddingButton(coordinator),
            RacklinkStopLoadSheddingButton(coordinator),
            RacklinkStartSequenceButton(coordinator),
            RacklinkStopSequenceButton(coordinator),
        ]
    )

    async_add_entities(entities)


class RacklinkButtonBase(CoordinatorEntity, ButtonEntity):
    """Base class for Middle Atlantic RackLink button entities."""

    def __init__(
        self,
        coordinator: RacklinkCoordinator,
        key: str,
        name: str,
        press_action: Callable,
        entity_category: str = None,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._key = key
        self._press_action = press_action

        # Set entity attributes
        self._attr_unique_id = f"{coordinator.controller.pdu_serial}_{key}"
        self._attr_name = name
        self._attr_entity_category = entity_category

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return self.coordinator.device_info

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.available

    async def async_press(self) -> None:
        """Press the button."""
        await self._press_action()


class RacklinkOutletCycleButton(RacklinkButtonBase):
    """Button to cycle power for a specific outlet."""

    def __init__(self, coordinator: RacklinkCoordinator, outlet_number: int) -> None:
        """Initialize the outlet cycle button."""

        async def cycle_this_outlet():
            await coordinator.cycle_outlet(outlet_number)

        super().__init__(
            coordinator=coordinator,
            key=f"outlet_{outlet_number}_cycle",
            name=f"Cycle Outlet {outlet_number}",
            press_action=cycle_this_outlet,
        )
        self._outlet_number = outlet_number

    @property
    def name(self) -> str:
        """Return the name of the button."""
        outlet_data = self.coordinator.outlet_data.get(self._outlet_number, {})
        outlet_name = outlet_data.get("name")
        if outlet_name and outlet_name != f"Outlet {self._outlet_number}":
            return f"Cycle {outlet_name}"
        return self._attr_name


class RacklinkAllOutletsCycleButton(RacklinkButtonBase):
    """Button to cycle power for all outlets."""

    def __init__(self, coordinator: RacklinkCoordinator) -> None:
        """Initialize the all outlets cycle button."""
        super().__init__(
            coordinator=coordinator,
            key="all_outlets_cycle",
            name="Cycle All Outlets",
            press_action=coordinator.cycle_all_outlets,
            entity_category=EntityCategory.CONFIG,
        )


class RacklinkStartLoadSheddingButton(RacklinkButtonBase):
    """Button to start load shedding."""

    def __init__(self, coordinator: RacklinkCoordinator) -> None:
        """Initialize the start load shedding button."""
        super().__init__(
            coordinator=coordinator,
            key="start_load_shedding",
            name="Start Load Shedding",
            press_action=coordinator.start_load_shedding,
            entity_category=EntityCategory.CONFIG,
        )


class RacklinkStopLoadSheddingButton(RacklinkButtonBase):
    """Button to stop load shedding."""

    def __init__(self, coordinator: RacklinkCoordinator) -> None:
        """Initialize the stop load shedding button."""
        super().__init__(
            coordinator=coordinator,
            key="stop_load_shedding",
            name="Stop Load Shedding",
            press_action=coordinator.stop_load_shedding,
            entity_category=EntityCategory.CONFIG,
        )


class RacklinkStartSequenceButton(RacklinkButtonBase):
    """Button to start the outlet sequence."""

    def __init__(self, coordinator: RacklinkCoordinator) -> None:
        """Initialize the start sequence button."""
        super().__init__(
            coordinator=coordinator,
            key="start_sequence",
            name="Start Sequence",
            press_action=coordinator.start_sequence,
            entity_category=EntityCategory.CONFIG,
        )


class RacklinkStopSequenceButton(RacklinkButtonBase):
    """Button to stop the outlet sequence."""

    def __init__(self, coordinator: RacklinkCoordinator) -> None:
        """Initialize the stop sequence button."""
        super().__init__(
            coordinator=coordinator,
            key="stop_sequence",
            name="Stop Sequence",
            press_action=coordinator.stop_sequence,
            entity_category=EntityCategory.CONFIG,
        )
