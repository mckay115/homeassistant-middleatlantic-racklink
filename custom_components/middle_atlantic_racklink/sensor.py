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

    # Add comprehensive power monitoring sensors
    entities.extend(
        [
            RacklinkVoltageSensor(coordinator),
            RacklinkCurrentSensor(coordinator),
            RacklinkPowerSensor(coordinator),
            RacklinkEnergySensor(coordinator),
            RacklinkFrequencySensor(coordinator),
            RacklinkApparentPowerSensor(coordinator),
            RacklinkPowerFactorSensor(coordinator),
        ]
    )

    # Add status sensors
    entities.extend(
        [
            RacklinkLoadSheddingSensor(coordinator),
            RacklinkSequenceSensor(coordinator),
        ]
    )

    # Add individual outlet power sensors for Redfish connections (no session corruption risk)
    if (
        hasattr(coordinator.controller, "connection_type")
        and coordinator.controller.connection_type == "redfish"
    ):
        outlets = coordinator.data.get("outlets", {})
        for outlet_id in outlets:
            entities.extend(
                [
                    RacklinkOutletPowerSensor(coordinator, outlet_id),
                    RacklinkOutletEnergySensor(coordinator, outlet_id),
                    RacklinkOutletCurrentSensor(coordinator, outlet_id),
                    RacklinkOutletVoltageSensor(coordinator, outlet_id),
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
        return self.coordinator.system_data.get("voltage")


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
                unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            ),
        )

    @property
    def native_value(self) -> float:
        """Return the energy value in kWh.

        Prefer kWh directly when the controller reports kWh; otherwise
        convert Wh to kWh if values appear large.
        """
        energy_val = self.coordinator.system_data.get("energy", 0)
        if energy_val is None:
            return 0
        # Heuristic: if the value is small (<100000), assume kWh and return as-is
        # If it's large, assume Wh and convert to kWh
        try:
            value = float(energy_val)
        except (TypeError, ValueError):
            return 0
        return value if value < 100000 else value / 1000


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


class RacklinkOutletPowerSensor(CoordinatorEntity, SensorEntity):
    """Sensor for individual outlet power consumption."""

    def __init__(self, coordinator: RacklinkCoordinator, outlet_id: int) -> None:
        """Initialize the outlet power sensor."""
        super().__init__(coordinator)
        self._outlet_id = outlet_id
        self._attr_unique_id = (
            f"{coordinator.data.get('device_id', 'unknown')}_{outlet_id}_power"
        )

        # Get outlet name for entity naming
        outlet_name = (
            coordinator.data.get("outlets", {})
            .get(outlet_id, {})
            .get("name", f"Outlet {outlet_id}")
        )
        self._attr_name = f"{outlet_name} Power"

        self._attr_device_class = SensorDeviceClass.POWER
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfPower.WATT
        self._attr_entity_category = None  # Main entity, not diagnostic

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return self.coordinator.device_info

    @property
    def native_value(self) -> float | None:
        """Return the outlet power consumption."""
        # This will be populated by the updated controller logic
        if hasattr(self.coordinator.controller, "outlet_power_data"):
            return self.coordinator.controller.outlet_power_data.get(self._outlet_id)
        return None

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        # Only available if outlet is powered on and we have data
        outlet_state = (
            self.coordinator.data.get("outlets", {})
            .get(self._outlet_id, {})
            .get("state", False)
        )
        return self.coordinator.available and outlet_state


class RacklinkApparentPowerSensor(RacklinkSensorBase):
    """Sensor for PDU apparent power (VA)."""

    def __init__(self, coordinator: RacklinkCoordinator) -> None:
        """Initialize the apparent power sensor."""
        super().__init__(
            coordinator=coordinator,
            config=SensorConfig(
                key="apparent_power",
                name="Apparent Power",
                device_class=SensorDeviceClass.APPARENT_POWER,
                state_class=SensorStateClass.MEASUREMENT,
                unit_of_measurement="VA",
            ),
        )

    @property
    def native_value(self) -> float:
        """Return the apparent power value."""
        return self.coordinator.system_data.get("apparent_power")


class RacklinkPowerFactorSensor(RacklinkSensorBase):
    """Sensor for PDU power factor."""

    def __init__(self, coordinator: RacklinkCoordinator) -> None:
        """Initialize the power factor sensor."""
        super().__init__(
            coordinator=coordinator,
            config=SensorConfig(
                key="power_factor",
                name="Power Factor",
                device_class=SensorDeviceClass.POWER_FACTOR,
                state_class=SensorStateClass.MEASUREMENT,
                unit_of_measurement=None,
            ),
        )

    @property
    def native_value(self) -> float:
        """Return the power factor value."""
        return self.coordinator.system_data.get("power_factor")


class RacklinkOutletEnergySensor(CoordinatorEntity, SensorEntity):
    """Sensor for individual outlet energy consumption."""

    def __init__(self, coordinator: RacklinkCoordinator, outlet_id: int) -> None:
        """Initialize the outlet energy sensor."""
        super().__init__(coordinator)
        self._outlet_id = outlet_id
        self._attr_unique_id = (
            f"{coordinator.data.get('device_id', 'unknown')}_{outlet_id}_energy"
        )

        # Get outlet name for entity naming
        outlet_name = (
            coordinator.data.get("outlets", {})
            .get(outlet_id, {})
            .get("name", f"Outlet {outlet_id}")
        )
        self._attr_name = f"{outlet_name} Energy"

        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_entity_category = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return self.coordinator.device_info

    @property
    def native_value(self) -> float | None:
        """Return the outlet energy consumption in kWh."""
        if hasattr(self.coordinator.controller, "outlet_energy_data"):
            energy_wh = self.coordinator.controller.outlet_energy_data.get(
                self._outlet_id, 0
            )
            return energy_wh / 1000 if energy_wh else 0
        return None

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.available


class RacklinkOutletCurrentSensor(CoordinatorEntity, SensorEntity):
    """Sensor for individual outlet current."""

    def __init__(self, coordinator: RacklinkCoordinator, outlet_id: int) -> None:
        """Initialize the outlet current sensor."""
        super().__init__(coordinator)
        self._outlet_id = outlet_id
        self._attr_unique_id = (
            f"{coordinator.data.get('device_id', 'unknown')}_{outlet_id}_current"
        )

        # Get outlet name for entity naming
        outlet_name = (
            coordinator.data.get("outlets", {})
            .get(outlet_id, {})
            .get("name", f"Outlet {outlet_id}")
        )
        self._attr_name = f"{outlet_name} Current"

        self._attr_device_class = SensorDeviceClass.CURRENT
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
        self._attr_entity_category = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return self.coordinator.device_info

    @property
    def native_value(self) -> float | None:
        """Return the outlet current."""
        if hasattr(self.coordinator.controller, "outlet_current_data"):
            return self.coordinator.controller.outlet_current_data.get(self._outlet_id)
        return None

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        outlet_state = (
            self.coordinator.data.get("outlets", {})
            .get(self._outlet_id, {})
            .get("state", False)
        )
        return self.coordinator.available and outlet_state


class RacklinkOutletVoltageSensor(CoordinatorEntity, SensorEntity):
    """Sensor for individual outlet voltage."""

    def __init__(self, coordinator: RacklinkCoordinator, outlet_id: int) -> None:
        """Initialize the outlet voltage sensor."""
        super().__init__(coordinator)
        self._outlet_id = outlet_id
        self._attr_unique_id = (
            f"{coordinator.data.get('device_id', 'unknown')}_{outlet_id}_voltage"
        )

        # Get outlet name for entity naming
        outlet_name = (
            coordinator.data.get("outlets", {})
            .get(outlet_id, {})
            .get("name", f"Outlet {outlet_id}")
        )
        self._attr_name = f"{outlet_name} Voltage"

        self._attr_device_class = SensorDeviceClass.VOLTAGE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
        self._attr_entity_category = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return self.coordinator.device_info

    @property
    def native_value(self) -> float | None:
        """Return the outlet voltage."""
        if hasattr(self.coordinator.controller, "outlet_voltage_data"):
            return self.coordinator.controller.outlet_voltage_data.get(self._outlet_id)
        return None

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        outlet_state = (
            self.coordinator.data.get("outlets", {})
            .get(self._outlet_id, {})
            .get("state", False)
        )
        return self.coordinator.available and outlet_state
