"""Sensor platform for Middle Atlantic Racklink."""

from __future__ import annotations

from homeassistant.helpers.entity import Entity
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfTemperature,
)
from .const import DOMAIN
from .racklink_controller import RacklinkController


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Middle Atlantic Racklink sensors."""
    controller = hass.data[DOMAIN][config_entry.entry_id]
    sensors = [
        RacklinkVoltage(controller),
        RacklinkCurrent(controller),
        RacklinkPower(controller),
        RacklinkEnergy(controller),
        RacklinkTemperature(controller),
        RacklinkFrequency(controller),
        RacklinkPowerFactor(controller),
    ]
    for i in range(1, 9):  # Assuming 8 outlets, adjust as needed
        sensors.extend(
            [
                RacklinkOutletPower(controller, i),
                RacklinkOutletCurrent(controller, i),
                RacklinkOutletEnergy(controller, i),
                RacklinkOutletPowerFactor(controller, i),
            ]
        )
    async_add_entities(sensors)


class RacklinkSensor(Entity):
    """Base class for Racklink sensors."""

    def __init__(
        self,
        controller,
        name: str,
        unit: str,
        sensor_type: str,
        device_class: str | None,
        state_class: str | None,
    ) -> None:
        """Initialize the sensor."""
        self._controller = controller
        self._name = name
        self._unit = unit
        self._state = None
        self._sensor_type = sensor_type
        self._device_class = device_class
        self._state_class = state_class

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self) -> float | None:
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return self._unit

    @property
    def device_class(self) -> str | None:
        """Return the device class."""
        return self._device_class

    @property
    def state_class(self) -> str | None:
        """Return the state class."""
        return self._state_class

    @property
    def unique_id(self) -> str:
        """Return the unique ID."""
        return f"{self._controller.pdu_serial}_{self._sensor_type}"

    @property
    def device_info(self) -> dict:
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._controller.pdu_serial)},
            "name": f"Racklink PDU {self._controller.pdu_name}",
            "manufacturer": "Middle Atlantic",
            "model": "Racklink PDU",
            "sw_version": self._controller.pdu_firmware,
        }


class RacklinkVoltage(RacklinkSensor):
    """Voltage sensor."""

    def __init__(self, controller) -> None:
        """Initialize the voltage sensor."""
        super().__init__(
            controller,
            "Racklink Voltage",
            UnitOfElectricPotential.VOLT,
            "voltage",
            SensorDeviceClass.VOLTAGE,
            SensorStateClass.MEASUREMENT,
        )

    async def async_update(self) -> None:
        """Update the sensor state."""
        self._state = self._controller.sensors.get("voltage")


class RacklinkCurrent(RacklinkSensor):
    """Current sensor."""

    def __init__(self, controller) -> None:
        """Initialize the current sensor."""
        super().__init__(
            controller,
            "Racklink Current",
            UnitOfElectricCurrent.AMPERE,
            "current",
            SensorDeviceClass.CURRENT,
            SensorStateClass.MEASUREMENT,
        )

    async def async_update(self) -> None:
        """Update the sensor state."""
        self._state = self._controller.sensors.get("current")


class RacklinkPower(RacklinkSensor):
    """Power sensor."""

    def __init__(self, controller) -> None:
        """Initialize the power sensor."""
        super().__init__(
            controller,
            "Racklink Power",
            UnitOfPower.WATT,
            "power",
            SensorDeviceClass.POWER,
            SensorStateClass.MEASUREMENT,
        )

    async def async_update(self) -> None:
        """Update the sensor state."""
        self._state = self._controller.sensors.get("power")


class RacklinkEnergy(RacklinkSensor):
    """Energy sensor."""

    def __init__(self, controller) -> None:
        """Initialize the energy sensor."""
        super().__init__(
            controller,
            "Racklink Energy",
            UnitOfEnergy.KILO_WATT_HOUR,
            "energy",
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL_INCREASING,
        )

    async def async_update(self) -> None:
        """Update the sensor state."""
        self._state = self._controller.sensors.get("energy")


class RacklinkTemperature(RacklinkSensor):
    """Temperature sensor."""

    def __init__(self, controller) -> None:
        """Initialize the temperature sensor."""
        super().__init__(
            controller,
            "Racklink Temperature",
            UnitOfTemperature.CELSIUS,
            "temperature",
            SensorDeviceClass.TEMPERATURE,
            SensorStateClass.MEASUREMENT,
        )

    async def async_update(self) -> None:
        """Update the sensor state."""
        self._state = self._controller.sensors.get("temperature")


class RacklinkFrequency(RacklinkSensor):
    """Frequency sensor."""

    def __init__(self, controller) -> None:
        """Initialize the frequency sensor."""
        super().__init__(
            controller,
            "Racklink Frequency",
            "Hz",
            "frequency",
            SensorDeviceClass.FREQUENCY,
            SensorStateClass.MEASUREMENT,
        )

    async def async_update(self) -> None:
        """Update the sensor state."""
        self._state = self._controller.sensors.get("frequency")


class RacklinkPowerFactor(RacklinkSensor):
    """Power factor sensor."""

    def __init__(self, controller) -> None:
        """Initialize the power factor sensor."""
        super().__init__(
            controller,
            "Racklink Power Factor",
            "%",
            "power_factor",
            SensorDeviceClass.POWER_FACTOR,
            SensorStateClass.MEASUREMENT,
        )

    async def async_update(self) -> None:
        """Update the sensor state."""
        self._state = self._controller.sensors.get("power_factor")


class RacklinkOutletPower(RacklinkSensor):
    """Outlet power sensor."""

    def __init__(self, controller, outlet: int) -> None:
        """Initialize the outlet power sensor."""
        super().__init__(
            controller,
            f"Outlet {outlet} Power",
            UnitOfPower.WATT,
            f"outlet_{outlet}_power",
            SensorDeviceClass.POWER,
            SensorStateClass.MEASUREMENT,
        )
        self._outlet = outlet

    async def async_update(self) -> None:
        """Update the sensor state."""
        self._state = self._controller.outlet_power.get(self._outlet)


class RacklinkOutletCurrent(RacklinkSensor):
    """Outlet current sensor."""

    def __init__(self, controller, outlet: int) -> None:
        """Initialize the outlet current sensor."""
        super().__init__(
            controller,
            f"Outlet {outlet} Current",
            UnitOfElectricCurrent.AMPERE,
            f"outlet_{outlet}_current",
            SensorDeviceClass.CURRENT,
            SensorStateClass.MEASUREMENT,
        )
        self._outlet = outlet

    async def async_update(self) -> None:
        """Update the sensor state."""
        self._state = self._controller.outlet_current.get(self._outlet)


class RacklinkOutletEnergy(RacklinkSensor):
    """Outlet energy sensor."""

    def __init__(self, controller, outlet: int) -> None:
        """Initialize the outlet energy sensor."""
        super().__init__(
            controller,
            f"Outlet {outlet} Energy",
            UnitOfEnergy.KILO_WATT_HOUR,
            f"outlet_{outlet}_energy",
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL_INCREASING,
        )
        self._outlet = outlet

    async def async_update(self) -> None:
        """Update the sensor state."""
        self._state = self._controller.outlet_energy.get(self._outlet)

    @property
    def last_reset(self) -> None:
        """Return the last reset time."""
        return None  # or return a specific datetime if you have this information


class RacklinkOutletPowerFactor(RacklinkSensor):
    """Outlet power factor sensor."""

    def __init__(self, controller, outlet: int) -> None:
        """Initialize the outlet power factor sensor."""
        super().__init__(
            controller,
            f"Outlet {outlet} Power Factor",
            "%",
            f"outlet_{outlet}_power_factor",
            SensorDeviceClass.POWER_FACTOR,
            SensorStateClass.MEASUREMENT,
        )
        self._outlet = outlet

    async def async_update(self) -> None:
        """Update the sensor state."""
        self._state = self._controller.outlet_power_factor.get(self._outlet)
