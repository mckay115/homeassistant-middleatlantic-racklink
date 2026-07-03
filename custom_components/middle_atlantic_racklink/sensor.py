"""Sensor platform for the Middle Atlantic RackLink integration."""

from __future__ import annotations

from . import RacklinkConfigEntry
from .const import DOMAIN
from .coordinator import RacklinkCoordinator
from collections.abc import Callable
from dataclasses import dataclass
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfApparentPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
)
from homeassistant.core import callback, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from typing import Any, Dict, Optional, Set

import logging

_LOGGER = logging.getLogger(__name__)


def _wh_to_kwh(value: Optional[float]) -> Optional[float]:
    """Convert a Wh reading to kWh, preserving None."""
    if value is None:
        return None
    return value / 1000


@dataclass(frozen=True, kw_only=True)
class RacklinkSensorEntityDescription(SensorEntityDescription):
    """Describes a RackLink sensor fed from a coordinator data dict."""

    value_fn: Callable[[Dict[str, Any]], StateType]


PDU_SENSORS: tuple[RacklinkSensorEntityDescription, ...] = (
    RacklinkSensorEntityDescription(
        key="voltage",
        translation_key="voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        value_fn=lambda data: data.get("voltage"),
    ),
    RacklinkSensorEntityDescription(
        key="current",
        translation_key="current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        value_fn=lambda data: data.get("current"),
    ),
    RacklinkSensorEntityDescription(
        key="power",
        translation_key="power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda data: data.get("power"),
    ),
    RacklinkSensorEntityDescription(
        key="energy",
        translation_key="energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda data: _wh_to_kwh(data.get("energy_wh")),
    ),
    RacklinkSensorEntityDescription(
        key="frequency",
        translation_key="frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        value_fn=lambda data: data.get("frequency"),
    ),
    RacklinkSensorEntityDescription(
        key="apparent_power",
        translation_key="apparent_power",
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        value_fn=lambda data: data.get("apparent_power"),
    ),
    RacklinkSensorEntityDescription(
        key="power_factor",
        translation_key="power_factor",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("power_factor"),
    ),
)

OUTLET_SENSORS: tuple[RacklinkSensorEntityDescription, ...] = (
    RacklinkSensorEntityDescription(
        key="power",
        translation_key="outlet_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda data: data.get("power"),
    ),
    RacklinkSensorEntityDescription(
        key="energy",
        translation_key="outlet_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda data: _wh_to_kwh(data.get("energy_wh")),
    ),
    RacklinkSensorEntityDescription(
        key="current",
        translation_key="outlet_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        value_fn=lambda data: data.get("current"),
    ),
    RacklinkSensorEntityDescription(
        key="voltage",
        translation_key="outlet_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        value_fn=lambda data: data.get("voltage"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: RacklinkConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Middle Atlantic RackLink sensors from a config entry."""
    coordinator = config_entry.runtime_data

    _migrate_legacy_outlet_unique_ids(hass, config_entry, coordinator)

    async_add_entities(
        RacklinkPduSensor(coordinator, description) for description in PDU_SENSORS
    )

    known_outlets: Set[int] = set()

    @callback
    def _add_outlet_entities() -> None:
        """Add sensors for outlets discovered on the device."""
        new_outlets = sorted(set(coordinator.outlet_data) - known_outlets)
        if not new_outlets:
            return
        known_outlets.update(new_outlets)
        async_add_entities(
            RacklinkOutletSensor(coordinator, description, outlet)
            for outlet in new_outlets
            for description in OUTLET_SENSORS
        )

    _add_outlet_entities()
    config_entry.async_on_unload(coordinator.async_add_listener(_add_outlet_entities))


def _migrate_legacy_outlet_unique_ids(
    hass: HomeAssistant,
    config_entry: RacklinkConfigEntry,
    coordinator: RacklinkCoordinator,
) -> None:
    """Migrate outlet sensor unique IDs from the legacy broken scheme.

    Older versions built outlet sensor unique IDs from a non-existent
    ``device_id`` key, producing IDs like ``unknown_1_power``. Rewrite them
    to the serial-based scheme so entity history is preserved.
    """
    registry = er.async_get(hass)
    serial = coordinator.controller.pdu_serial
    if not serial:
        return

    for outlet in coordinator.outlet_data:
        for description in OUTLET_SENSORS:
            legacy_unique_id = f"unknown_{outlet}_{description.key}"
            entity_id = registry.async_get_entity_id("sensor", DOMAIN, legacy_unique_id)
            if entity_id is None:
                continue
            entry = registry.async_get(entity_id)
            if entry is None or entry.config_entry_id != config_entry.entry_id:
                continue
            new_unique_id = f"{serial}_outlet_{outlet}_{description.key}"
            if registry.async_get_entity_id("sensor", DOMAIN, new_unique_id):
                continue
            _LOGGER.debug(
                "Migrating unique ID %s -> %s for %s",
                legacy_unique_id,
                new_unique_id,
                entity_id,
            )
            registry.async_update_entity(entity_id, new_unique_id=new_unique_id)


class RacklinkSensorBase(CoordinatorEntity[RacklinkCoordinator], SensorEntity):
    """Base class for Middle Atlantic RackLink sensor entities."""

    entity_description: RacklinkSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RacklinkCoordinator,
        description: RacklinkSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
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


class RacklinkPduSensor(RacklinkSensorBase):
    """PDU-level sensor reading from the coordinator system data."""

    def __init__(
        self,
        coordinator: RacklinkCoordinator,
        description: RacklinkSensorEntityDescription,
    ) -> None:
        """Initialize the PDU sensor."""
        super().__init__(coordinator, description)
        self._attr_unique_id = f"{coordinator.controller.pdu_serial}_{description.key}"

    @property
    def native_value(self) -> StateType:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.system_data)


class RacklinkOutletSensor(RacklinkSensorBase):
    """Per-outlet sensor reading from the coordinator outlet data."""

    def __init__(
        self,
        coordinator: RacklinkCoordinator,
        description: RacklinkSensorEntityDescription,
        outlet: int,
    ) -> None:
        """Initialize the outlet sensor."""
        super().__init__(coordinator, description)
        self._outlet = outlet
        self._attr_unique_id = (
            f"{coordinator.controller.pdu_serial}_outlet_{outlet}_{description.key}"
        )
        self._attr_translation_placeholders = {"outlet_number": str(outlet)}

    @property
    def native_value(self) -> StateType:
        """Return the sensor value."""
        outlet_data = self.coordinator.outlet_data.get(self._outlet, {})
        return self.entity_description.value_fn(outlet_data)
