"""Number platform for the Middle Atlantic RackLink integration."""

from __future__ import annotations

from . import RacklinkConfigEntry
from .const import MAX_SEQUENCE_DELAY, MIN_SEQUENCE_DELAY
from .coordinator import RacklinkCoordinator
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

import logging

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: RacklinkConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Middle Atlantic RackLink number entities."""
    coordinator = config_entry.runtime_data
    controller = coordinator.controller

    # Sequencing is configured over the telnet channel
    if controller.enable_vendor_features and controller.has_vendor_features:
        async_add_entities([RacklinkSequenceDelayNumber(coordinator)])


class RacklinkSequenceDelayNumber(CoordinatorEntity[RacklinkCoordinator], NumberEntity):
    """Configurable delay between outlets during a power-on sequence."""

    _attr_has_entity_name = True
    _attr_translation_key = "sequence_delay"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = MIN_SEQUENCE_DELAY
    _attr_native_max_value = MAX_SEQUENCE_DELAY
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS

    def __init__(self, coordinator: RacklinkCoordinator) -> None:
        """Initialize the sequence delay number."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.controller.pdu_serial}_sequence_delay"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return self.coordinator.device_info

    @property
    def native_value(self) -> int:
        """Return the configured sequence delay."""
        return self.coordinator.sequence_delay

    async def async_set_native_value(self, value: float) -> None:
        """Persist a new sequence delay."""
        await self.coordinator.async_set_sequence_delay(int(value))
        self.async_write_ha_state()
