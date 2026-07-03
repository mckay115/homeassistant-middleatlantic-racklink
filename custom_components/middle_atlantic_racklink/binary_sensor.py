"""Binary sensor platform for the Middle Atlantic RackLink integration."""

from __future__ import annotations

from . import RacklinkConfigEntry
from .coordinator import RacklinkCoordinator
from collections.abc import Callable
from dataclasses import dataclass
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import callback, HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from typing import Any, Dict, Optional, Set

import logging

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class RacklinkBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a RackLink binary sensor fed from a coordinator data dict."""

    value_fn: Callable[[Dict[str, Any]], Optional[bool]]


STATUS_BINARY_SENSORS: tuple[RacklinkBinarySensorEntityDescription, ...] = (
    RacklinkBinarySensorEntityDescription(
        key="surge_protection",
        translation_key="surge_protection",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("surge_protection_ok"),
    ),
    RacklinkBinarySensorEntityDescription(
        key="load_shedding",
        translation_key="load_shedding",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("load_shedding_active"),
    ),
    RacklinkBinarySensorEntityDescription(
        key="sequence",
        translation_key="sequence",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("sequence_active"),
    ),
)

OUTLET_BINARY_SENSORS: tuple[RacklinkBinarySensorEntityDescription, ...] = (
    RacklinkBinarySensorEntityDescription(
        key="non_critical",
        translation_key="outlet_non_critical",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("non_critical"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: RacklinkConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Middle Atlantic RackLink binary sensors."""
    coordinator = config_entry.runtime_data

    async_add_entities(
        RacklinkStatusBinarySensor(coordinator, description)
        for description in STATUS_BINARY_SENSORS
    )

    known_outlets: Set[int] = set()

    @callback
    def _add_outlet_entities() -> None:
        """Add binary sensors for outlets discovered on the device."""
        new_outlets = sorted(set(coordinator.outlet_data) - known_outlets)
        if not new_outlets:
            return
        known_outlets.update(new_outlets)
        async_add_entities(
            RacklinkOutletBinarySensor(coordinator, description, outlet)
            for outlet in new_outlets
            for description in OUTLET_BINARY_SENSORS
        )

    _add_outlet_entities()
    config_entry.async_on_unload(coordinator.async_add_listener(_add_outlet_entities))


class RacklinkBinarySensorBase(
    CoordinatorEntity[RacklinkCoordinator], BinarySensorEntity
):
    """Base class for RackLink binary sensors."""

    entity_description: RacklinkBinarySensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RacklinkCoordinator,
        description: RacklinkBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self.entity_description = description

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return self.coordinator.device_info

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return super().available and self.coordinator.controller.connected


class RacklinkStatusBinarySensor(RacklinkBinarySensorBase):
    """PDU status binary sensor reading from the coordinator status data."""

    def __init__(
        self,
        coordinator: RacklinkCoordinator,
        description: RacklinkBinarySensorEntityDescription,
    ) -> None:
        """Initialize the status binary sensor."""
        super().__init__(coordinator, description)
        self._attr_unique_id = f"{coordinator.controller.pdu_serial}_{description.key}"

    @property
    def is_on(self) -> Optional[bool]:
        """Return the state of the binary sensor."""
        return self.entity_description.value_fn(self.coordinator.status_data)


class RacklinkOutletBinarySensor(RacklinkBinarySensorBase):
    """Per-outlet binary sensor reading from the coordinator outlet data."""

    def __init__(
        self,
        coordinator: RacklinkCoordinator,
        description: RacklinkBinarySensorEntityDescription,
        outlet: int,
    ) -> None:
        """Initialize the outlet binary sensor."""
        super().__init__(coordinator, description)
        self._outlet = outlet
        self._attr_unique_id = (
            f"{coordinator.controller.pdu_serial}_outlet_{outlet}_{description.key}"
        )
        self._attr_translation_placeholders = {"outlet_number": str(outlet)}

    @property
    def is_on(self) -> Optional[bool]:
        """Return the state of the binary sensor."""
        outlet_data = self.coordinator.outlet_data.get(self._outlet, {})
        return self.entity_description.value_fn(outlet_data)
