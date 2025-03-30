"""Sensor implementation for Middle Atlantic Racklink integration."""

import logging
from typing import Any, Optional, Union

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
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_MANUFACTURER,
    ATTR_MODEL,
    DOMAIN,
    OUTLET_METRIC_APPARENT_POWER,
    OUTLET_METRIC_CURRENT,
    OUTLET_METRIC_ENERGY,
    OUTLET_METRIC_FREQUENCY,
    OUTLET_METRIC_POWER,
    OUTLET_METRIC_POWER_FACTOR,
    OUTLET_METRIC_VOLTAGE,
    SENSOR_PDU_CURRENT,
    SENSOR_PDU_ENERGY,
    SENSOR_PDU_FREQUENCY,
    SENSOR_PDU_POWER,
    SENSOR_PDU_POWER_FACTOR,
    SENSOR_PDU_TEMPERATURE,
    SENSOR_PDU_VOLTAGE,
)
from .coordinator import RacklinkCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors for Racklink controller."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data["coordinator"]
    controller = data["controller"]

    entities = []

    # Add PDU sensors
    entities.extend(
        [
            RacklinkPower(coordinator),
            RacklinkCurrent(coordinator),
            RacklinkVoltage(coordinator),
            RacklinkEnergy(coordinator),
            RacklinkPowerFactor(coordinator),
            RacklinkTemperature(coordinator),
            RacklinkFrequency(coordinator),
        ]
    )

    # Add outlet power sensors if the model supports power sensing
    capabilities = controller.get_model_capabilities()
    if capabilities.get("has_current_sensing", False):
        _LOGGER.info("Adding outlet power sensors")

        # Get number of outlets
        num_outlets = capabilities.get("num_outlets", 8)

        # Add a power sensor for each outlet
        for outlet_num in range(1, num_outlets + 1):
            entities.append(RacklinkOutletPowerSensor(coordinator, outlet_num))
            entities.append(RacklinkOutletCurrentSensor(coordinator, outlet_num))

    async_add_entities(entities)


class RacklinkSensor(CoordinatorEntity, SensorEntity):
    """Base class for Racklink sensors."""

    def __init__(
        self,
        coordinator: RacklinkCoordinator,
        name: str,
        unit: str,
        sensor_type: str,
        device_class: str | None,
        state_class: str | None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._controller = coordinator.controller
        self._attr_name = name
        self._unit = unit
        self._state = None
        self._sensor_type = sensor_type
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        self._attr_unique_id = f"{self._controller.pdu_serial}_{self._sensor_type}"
        self._attr_has_entity_name = True
        self._attr_available = False

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return self._attr_name

    @property
    def state(self) -> float | None:
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return self._unit

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self._controller.connected
            and self._controller.available
        )

    @property
    def state_class(self) -> str | None:
        """Return the state class of the sensor."""
        return self._attr_state_class

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        device_info = {
            "identifiers": {(DOMAIN, self._controller.pdu_serial)},
            "name": f"Racklink PDU {self._controller.pdu_name}",
            "manufacturer": ATTR_MANUFACTURER,
            "model": self._controller.pdu_model or ATTR_MODEL,
            "sw_version": self._controller.pdu_firmware,
        }

        # Add MAC address as a connection info if available
        if self._controller.mac_address:
            device_info["connections"] = {("mac", self._controller.mac_address)}

        return device_info


class RacklinkCurrent(RacklinkSensor):
    """Current sensor."""

    def __init__(self, coordinator) -> None:
        """Initialize the current sensor."""
        super().__init__(
            coordinator,
            "Current",
            UnitOfElectricCurrent.AMPERE,
            "current",
            SensorDeviceClass.CURRENT,
            SensorStateClass.MEASUREMENT,
        )

    @property
    def state(self) -> Optional[float]:
        """Return the state of the sensor."""
        if self.coordinator.data and "sensors" in self.coordinator.data:
            return self.coordinator.data["sensors"].get(SENSOR_PDU_CURRENT)
        return None


class RacklinkPower(RacklinkSensor):
    """Power sensor."""

    def __init__(self, coordinator) -> None:
        """Initialize the power sensor."""
        super().__init__(
            coordinator,
            "Power",
            UnitOfPower.WATT,
            "power",
            SensorDeviceClass.POWER,
            SensorStateClass.MEASUREMENT,
        )

    @property
    def state(self) -> Optional[float]:
        """Return the state of the sensor."""
        if self.coordinator.data and "sensors" in self.coordinator.data:
            return self.coordinator.data["sensors"].get(SENSOR_PDU_POWER)
        return None


class RacklinkVoltage(RacklinkSensor):
    """Voltage sensor."""

    def __init__(self, coordinator) -> None:
        """Initialize the voltage sensor."""
        super().__init__(
            coordinator,
            "Voltage",
            UnitOfElectricPotential.VOLT,
            "voltage",
            SensorDeviceClass.VOLTAGE,
            SensorStateClass.MEASUREMENT,
        )

    @property
    def state(self) -> Optional[float]:
        """Return the state of the sensor."""
        if self.coordinator.data and "sensors" in self.coordinator.data:
            return self.coordinator.data["sensors"].get(SENSOR_PDU_VOLTAGE)
        return None


class RacklinkEnergy(RacklinkSensor):
    """Energy sensor."""

    def __init__(self, coordinator) -> None:
        """Initialize the energy sensor."""
        super().__init__(
            coordinator,
            "Energy",
            UnitOfEnergy.KILO_WATT_HOUR,
            "energy",
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL_INCREASING,
        )

    @property
    def state(self) -> Optional[float]:
        """Return the state of the sensor."""
        if self.coordinator.data and "sensors" in self.coordinator.data:
            # Energy is typically reported in Wh but we show kWh
            energy_wh = self.coordinator.data["sensors"].get(SENSOR_PDU_ENERGY)
            if energy_wh is not None:
                return energy_wh / 1000  # Convert Wh to kWh
        return None


class RacklinkPowerFactor(RacklinkSensor):
    """Power factor sensor."""

    def __init__(self, coordinator) -> None:
        """Initialize the power factor sensor."""
        super().__init__(
            coordinator,
            "Power Factor",
            "%",
            "power_factor",
            SensorDeviceClass.POWER_FACTOR,
            SensorStateClass.MEASUREMENT,
        )

    @property
    def state(self) -> Optional[float]:
        """Return the state of the sensor."""
        if self.coordinator.data and "sensors" in self.coordinator.data:
            pf = self.coordinator.data["sensors"].get(SENSOR_PDU_POWER_FACTOR)
            if pf is not None:
                return pf * 100  # Convert to percentage
        return None


class RacklinkTemperature(RacklinkSensor):
    """Temperature sensor."""

    def __init__(self, coordinator) -> None:
        """Initialize the temperature sensor."""
        super().__init__(
            coordinator,
            "Temperature",
            UnitOfTemperature.CELSIUS,
            "temperature",
            SensorDeviceClass.TEMPERATURE,
            SensorStateClass.MEASUREMENT,
        )

    @property
    def state(self) -> Optional[float]:
        """Return the state of the sensor."""
        if self.coordinator.data and "sensors" in self.coordinator.data:
            return self.coordinator.data["sensors"].get(SENSOR_PDU_TEMPERATURE)
        return None


class RacklinkFrequency(RacklinkSensor):
    """Frequency sensor."""

    def __init__(self, coordinator) -> None:
        """Initialize the frequency sensor."""
        super().__init__(
            coordinator,
            "Frequency",
            UnitOfFrequency.HERTZ,
            "frequency",
            SensorDeviceClass.FREQUENCY,
            SensorStateClass.MEASUREMENT,
        )

    @property
    def state(self) -> Optional[float]:
        """Return the state of the sensor."""
        if self.coordinator.data and "sensors" in self.coordinator.data:
            return self.coordinator.data["sensors"].get(SENSOR_PDU_FREQUENCY)
        return None


class RacklinkOutletPowerSensor(RacklinkSensor):
    """Power sensor for a specific outlet."""

    def __init__(self, coordinator, outlet_num) -> None:
        """Initialize the outlet power sensor."""
        self._outlet_num = outlet_num
        super().__init__(
            coordinator,
            f"Outlet {outlet_num} Power",
            UnitOfPower.WATT,
            f"outlet_{outlet_num}_power",
            SensorDeviceClass.POWER,
            SensorStateClass.MEASUREMENT,
        )

    @property
    def state(self) -> Optional[float]:
        """Return the state of the sensor."""
        if self.coordinator.data and "outlets" in self.coordinator.data:
            outlet_data = self.coordinator.data["outlets"].get(self._outlet_num, {})
            return outlet_data.get("power")
        return None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        # Return the main PDU device info so all outlet sensors are grouped together
        return super().device_info


class RacklinkOutletCurrentSensor(RacklinkSensor):
    """Current sensor for a specific outlet."""

    def __init__(self, coordinator, outlet_num) -> None:
        """Initialize the outlet current sensor."""
        self._outlet_num = outlet_num
        super().__init__(
            coordinator,
            f"Outlet {outlet_num} Current",
            UnitOfElectricCurrent.AMPERE,
            f"outlet_{outlet_num}_current",
            SensorDeviceClass.CURRENT,
            SensorStateClass.MEASUREMENT,
        )

    @property
    def state(self) -> Optional[float]:
        """Return the state of the sensor."""
        if self.coordinator.data and "outlets" in self.coordinator.data:
            outlet_data = self.coordinator.data["outlets"].get(self._outlet_num, {})
            return outlet_data.get("current")
        return None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        # Return the main PDU device info so all outlet sensors are grouped together
        return super().device_info
