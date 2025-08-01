"""Sensor platform for the Middle Atlantic RackLink integration."""

from __future__ import annotations

# Standard library imports
from dataclasses import dataclass
import logging
from typing import Optional

# Home Assistant core imports
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

# Local application/library specific imports
from . import DOMAIN
from .coordinator import RacklinkCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass
class SensorConfig:
    """Configuration for a sensor entity."""

    key: str
    name: str
    device_class: Optional[str] = None
    state_class: Optional[str] = None
    unit_of_measurement: Optional[str] = None
    entity_category: Optional[str] = None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Middle Atlantic RackLink sensors from config entry."""
    coordinator: RacklinkCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities = []

    # Add system power sensors
    entities.extend(
        [
            RacklinkVoltageSensor(coordinator),
            RacklinkCurrentSensor(coordinator),
            RacklinkPowerSensor(coordinator),
            RacklinkEnergySensor(coordinator),
            RacklinkFrequencySensor(coordinator),
        ]
    )

    # Add status sensors
    entities.extend(
        [
            RacklinkLoadSheddingSensor(coordinator),
            RacklinkSequenceSensor(coordinator),
        ]
    )

    async_add_entities(entities)


class RacklinkSensorBase(CoordinatorEntity, SensorEntity):
    """Base class for Middle Atlantic RackLink sensor entities."""

    def __init__(
        self,
        coordinator: RacklinkCoordinator,
        config: SensorConfig,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._key = config.key

        # Set entity attributes
        self._attr_unique_id = f"{coordinator.controller.pdu_serial}_{config.key}"
        self._attr_name = config.name
        self._attr_device_class = config.device_class
        self._attr_state_class = config.state_class
        self._attr_native_unit_of_measurement = config.unit_of_measurement
        self._attr_entity_category = config.entity_category

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return self.coordinator.device_info

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.available


class RacklinkVoltageSensor(RacklinkSensorBase):
    """Sensor for PDU voltage."""

    def __init__(self, coordinator: RacklinkCoordinator) -> None:
        """Initialize the voltage sensor."""
        super().__init__(
            coordinator=coordinator,
            config=SensorConfig(
                key="voltage",
                name="Voltage",
                device_class=SensorDeviceClass.VOLTAGE,
                state_class=SensorStateClass.MEASUREMENT,
                unit_of_measurement=UnitOfElectricPotential.VOLT,
            ),
        )

    @property
    def native_value(self) -> float:
        """Return the voltage value."""
        value = self.coordinator.system_data.get("voltage")
        _LOGGER.debug(
            "🔋 Voltage sensor value: %s (from system_data: %s)",
            value,
            self.coordinator.system_data,
        )
        return value


class RacklinkCurrentSensor(RacklinkSensorBase):
    """Sensor for PDU current."""

    def __init__(self, coordinator: RacklinkCoordinator) -> None:
        """Initialize the current sensor."""
        super().__init__(
            coordinator=coordinator,
            config=SensorConfig(
                key="current",
                name="Current",
                device_class=SensorDeviceClass.CURRENT,
                state_class=SensorStateClass.MEASUREMENT,
                unit_of_measurement=UnitOfElectricCurrent.AMPERE,
            ),
        )

    @property
    def native_value(self) -> float:
        """Return the current value."""
        return self.coordinator.system_data.get("current")


class RacklinkPowerSensor(RacklinkSensorBase):
    """Sensor for PDU power."""

    def __init__(self, coordinator: RacklinkCoordinator) -> None:
        """Initialize the power sensor."""
        super().__init__(
            coordinator=coordinator,
            config=SensorConfig(
                key="power",
                name="Power",
                device_class=SensorDeviceClass.POWER,
                state_class=SensorStateClass.MEASUREMENT,
                unit_of_measurement=UnitOfPower.WATT,
            ),
        )

    @property
    def native_value(self) -> float:
        """Return the power value."""
        return self.coordinator.system_data.get("power")


class RacklinkEnergySensor(RacklinkSensorBase):
    """Sensor for PDU energy."""

    def __init__(self, coordinator: RacklinkCoordinator) -> None:
        """Initialize the energy sensor."""
        super().__init__(
            coordinator=coordinator,
            config=SensorConfig(
                key="energy",
                name="Energy",
                device_class=SensorDeviceClass.ENERGY,
                state_class=SensorStateClass.TOTAL_INCREASING,
                unit_of_measurement=UnitOfEnergy.WATT_HOUR,
            ),
        )

    @property
    def native_value(self) -> float:
        """Return the energy value."""
        return self.coordinator.system_data.get("energy")


class RacklinkFrequencySensor(RacklinkSensorBase):
    """Sensor for PDU frequency."""

    def __init__(self, coordinator: RacklinkCoordinator) -> None:
        """Initialize the frequency sensor."""
        super().__init__(
            coordinator=coordinator,
            config=SensorConfig(
                key="frequency",
                name="Line Frequency",
                device_class=SensorDeviceClass.FREQUENCY,
                state_class=SensorStateClass.MEASUREMENT,
                unit_of_measurement=UnitOfFrequency.HERTZ,
            ),
        )

    @property
    def native_value(self) -> float:
        """Return the frequency value."""
        return self.coordinator.system_data.get("frequency")


class RacklinkLoadSheddingSensor(RacklinkSensorBase):
    """Sensor for load shedding status."""

    def __init__(self, coordinator: RacklinkCoordinator) -> None:
        """Initialize the load shedding sensor."""
        super().__init__(
            coordinator=coordinator,
            config=SensorConfig(
                key="load_shedding",
                name="Load Shedding",
                entity_category=EntityCategory.DIAGNOSTIC,
            ),
        )

    @property
    def native_value(self) -> str:
        """Return the load shedding state."""
        return (
            "Active"
            if self.coordinator.status_data.get("load_shedding_active")
            else "Inactive"
        )


class RacklinkSequenceSensor(RacklinkSensorBase):
    """Sensor for sequence status."""

    def __init__(self, coordinator: RacklinkCoordinator) -> None:
        """Initialize the sequence sensor."""
        super().__init__(
            coordinator=coordinator,
            config=SensorConfig(
                key="sequence",
                name="Sequence",
                entity_category=EntityCategory.DIAGNOSTIC,
            ),
        )

    @property
    def native_value(self) -> str:
        """Return the sequence state."""
        return (
            "Running"
            if self.coordinator.status_data.get("sequence_active")
            else "Stopped"
        )
