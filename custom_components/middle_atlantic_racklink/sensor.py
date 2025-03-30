"""Sensor platform for Middle Atlantic Racklink."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ATTR_MANUFACTURER, ATTR_MODEL, DOMAIN
from .racklink_controller import RacklinkController

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Middle Atlantic Racklink sensors."""
    controller = hass.data[DOMAIN][config_entry.entry_id]

    # Get model capabilities to determine number of outlets
    capabilities = controller.get_model_capabilities()
    outlet_count = capabilities.get("num_outlets", 8)  # Default to 8 if not determined

    _LOGGER.info(
        "Setting up %d outlets for %s (%s)",
        outlet_count,
        controller.pdu_name,
        controller.pdu_model,
    )

    sensors = [
        RacklinkVoltage(controller),
        RacklinkCurrent(controller),
        RacklinkPower(controller),
        RacklinkEnergy(controller),
        RacklinkTemperature(controller),
        RacklinkFrequency(controller),
        RacklinkPowerFactor(controller),
    ]

    # Create sensors for each outlet
    for i in range(1, outlet_count + 1):
        sensors.extend(
            [
                RacklinkOutletPower(controller, i),
                RacklinkOutletCurrent(controller, i),
                RacklinkOutletEnergy(controller, i),
                RacklinkOutletPowerFactor(controller, i),
                RacklinkOutletApparentPower(controller, i),
                RacklinkOutletVoltage(controller, i),
                RacklinkOutletLineFrequency(controller, i),
            ]
        )

    async_add_entities(sensors)


class RacklinkSensor(Entity):
    """Base class for Racklink sensors."""

    def __init__(
        self,
        controller: RacklinkController,
        name: str,
        unit: str,
        sensor_type: str,
        device_class: str | None,
        state_class: str | None,
    ) -> None:
        """Initialize the sensor."""
        self._controller = controller
        self._attr_name = name
        self._unit = unit
        self._state = None
        self._sensor_type = sensor_type
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        self._attr_unique_id = f"{controller.pdu_serial}_{self._sensor_type}"
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
        return self._controller.connected and self._controller.available

    @property
    def state_class(self) -> str | None:
        """Return the state class of the sensor."""
        return self._attr_state_class

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._controller.pdu_serial)},
            "name": f"Racklink PDU {self._controller.pdu_name}",
            "manufacturer": ATTR_MANUFACTURER,
            "model": self._controller.pdu_model or ATTR_MODEL,
            "sw_version": self._controller.pdu_firmware,
        }

    async def async_added_to_hass(self) -> None:
        """Register callbacks when entity is added."""
        # Listen for entity updates from the dispatcher signal
        self.async_on_remove(
            self.hass.helpers.dispatcher.async_dispatcher_connect(
                f"{DOMAIN}_entity_update", self.async_write_ha_state
            )
        )

    async def async_update(self) -> None:
        """Update method to be implemented by derived classes."""
        raise NotImplementedError("Subclasses must implement async_update")


class RacklinkVoltage(RacklinkSensor):
    """Voltage sensor."""

    def __init__(self, controller: RacklinkController) -> None:
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
        if not self._controller.connected:
            self._attr_available = False
            return

        try:
            self._state = self._controller.sensors.get("voltage")
            self._attr_available = (
                self._controller.connected and self._controller.available
            )

            if self._state is None and self._controller.connected:
                # If we're connected but don't have data, it may be stale
                _LOGGER.debug("Voltage data is missing, may need refresh")
        except Exception as err:
            _LOGGER.error("Error updating voltage sensor: %s", err)
            self._state = None
            self._attr_available = False


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
        if not self._controller.connected:
            self._attr_available = False
            return

        try:
            self._state = self._controller.sensors.get("current")
            self._attr_available = (
                self._controller.connected and self._controller.available
            )
        except Exception as err:
            _LOGGER.error("Error updating current sensor: %s", err)
            self._state = None
            self._attr_available = False


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
        if not self._controller.connected:
            self._attr_available = False
            return

        try:
            self._state = self._controller.sensors.get("power")
            self._attr_available = (
                self._controller.connected and self._controller.available
            )
        except Exception as err:
            _LOGGER.error("Error updating power sensor: %s", err)
            self._state = None
            self._attr_available = False


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
        if not self._controller.connected:
            self._attr_available = False
            return

        try:
            # Get energy value and convert from Wh to kWh if needed
            energy_wh = self._controller.sensors.get("energy")
            if energy_wh is not None:
                self._state = energy_wh / 1000.0  # Convert Wh to kWh
                self._attr_available = (
                    self._controller.connected and self._controller.available
                )
            else:
                self._state = None
                self._attr_available = False
        except Exception as err:
            _LOGGER.error("Error updating energy sensor: %s", err)
            self._state = None
            self._attr_available = False

    @property
    def last_reset(self) -> None:
        """Return the last reset time."""
        return None  # PDU energy values are typically since power-on


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
        if not self._controller.connected:
            self._attr_available = False
            return

        try:
            self._state = self._controller.sensors.get("temperature")
            self._attr_available = (
                self._controller.connected and self._controller.available
            )
        except Exception as err:
            _LOGGER.error("Error updating temperature sensor: %s", err)
            self._state = None
            self._attr_available = False


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
        if not self._controller.connected:
            self._attr_available = False
            return

        try:
            self._state = self._controller.sensors.get("frequency")
            self._attr_available = (
                self._controller.connected and self._controller.available
            )
        except Exception as err:
            _LOGGER.error("Error updating frequency sensor: %s", err)
            self._state = None
            self._attr_available = False


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
        if not self._controller.connected:
            self._attr_available = False
            return

        try:
            self._state = self._controller.sensors.get("power_factor")
            self._attr_available = (
                self._controller.connected and self._controller.available
            )
        except Exception as err:
            _LOGGER.error("Error updating power factor sensor: %s", err)
            self._state = None
            self._attr_available = False


class RacklinkOutletPower(RacklinkSensor):
    """Outlet power sensor."""

    _attr_has_entity_name = True

    def __init__(self, controller: RacklinkController, outlet: int) -> None:
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
        self._attr_entity_registry_enabled_default = True

    async def async_update(self) -> None:
        """Update the sensor state."""
        if not self._controller.connected:
            self._attr_available = False
            return

        try:
            self._state = self._controller.outlet_power.get(self._outlet)
            self._attr_available = (
                self._controller.connected
                and self._controller.available
                and self._state is not None
            )
        except Exception as err:
            _LOGGER.error(
                "Error updating outlet %d power sensor: %s", self._outlet, err
            )
            self._state = None
            self._attr_available = False


class RacklinkOutletCurrent(RacklinkSensor):
    """Outlet current sensor."""

    _attr_has_entity_name = True

    def __init__(self, controller: RacklinkController, outlet: int) -> None:
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
        self._attr_entity_registry_enabled_default = True

    async def async_update(self) -> None:
        """Update the sensor state."""
        if not self._controller.connected:
            self._attr_available = False
            return

        try:
            self._state = self._controller.outlet_current.get(self._outlet)
            self._attr_available = (
                self._controller.connected
                and self._controller.available
                and self._state is not None
            )
        except Exception as err:
            _LOGGER.error(
                "Error updating outlet %d current sensor: %s", self._outlet, err
            )
            self._state = None
            self._attr_available = False


class RacklinkOutletEnergy(RacklinkSensor):
    """Outlet energy sensor."""

    _attr_has_entity_name = True

    def __init__(self, controller: RacklinkController, outlet: int) -> None:
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
        self._attr_entity_registry_enabled_default = True

    async def async_update(self) -> None:
        """Update the sensor state."""
        if not self._controller.connected:
            self._attr_available = False
            return

        try:
            self._state = self._controller.outlet_energy.get(self._outlet)
            self._attr_available = (
                self._controller.connected
                and self._controller.available
                and self._state is not None
            )
        except Exception as err:
            _LOGGER.error(
                "Error updating outlet %d energy sensor: %s", self._outlet, err
            )
            self._state = None
            self._attr_available = False


class RacklinkOutletPowerFactor(RacklinkSensor):
    """Outlet power factor sensor."""

    _attr_has_entity_name = True

    def __init__(self, controller: RacklinkController, outlet: int) -> None:
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
        self._attr_entity_registry_enabled_default = True

    async def async_update(self) -> None:
        """Update the sensor state."""
        if not self._controller.connected:
            self._attr_available = False
            return

        try:
            self._state = self._controller.outlet_power_factor.get(self._outlet)
            if self._state is not None:
                # Convert to percentage (0-100) for power factor
                self._state = self._state * 100
            self._attr_available = (
                self._controller.connected
                and self._controller.available
                and self._state is not None
            )
        except Exception as err:
            _LOGGER.error(
                "Error updating outlet %d power factor sensor: %s", self._outlet, err
            )
            self._state = None
            self._attr_available = False


class RacklinkOutletApparentPower(RacklinkSensor):
    """Outlet apparent power sensor."""

    _attr_has_entity_name = True

    def __init__(self, controller: RacklinkController, outlet: int) -> None:
        """Initialize the outlet apparent power sensor."""
        super().__init__(
            controller,
            f"Outlet {outlet} Apparent Power",
            "VA",
            f"outlet_{outlet}_apparent_power",
            None,
            SensorStateClass.MEASUREMENT,
        )
        self._outlet = outlet
        self._attr_entity_registry_enabled_default = True

    async def async_update(self) -> None:
        """Update the sensor state."""
        if not self._controller.connected:
            self._attr_available = False
            return

        try:
            self._state = self._controller.outlet_apparent_power.get(self._outlet)
            self._attr_available = (
                self._controller.connected
                and self._controller.available
                and self._state is not None
            )
        except Exception as err:
            _LOGGER.error(
                "Error updating outlet %d apparent power sensor: %s", self._outlet, err
            )
            self._state = None
            self._attr_available = False


class RacklinkOutletVoltage(RacklinkSensor):
    """Outlet voltage sensor."""

    _attr_has_entity_name = True

    def __init__(self, controller: RacklinkController, outlet: int) -> None:
        """Initialize the outlet voltage sensor."""
        super().__init__(
            controller,
            f"Outlet {outlet} Voltage",
            UnitOfElectricPotential.VOLT,
            f"outlet_{outlet}_voltage",
            SensorDeviceClass.VOLTAGE,
            SensorStateClass.MEASUREMENT,
        )
        self._outlet = outlet
        self._attr_entity_registry_enabled_default = True

    async def async_update(self) -> None:
        """Update the sensor state."""
        if not self._controller.connected:
            self._attr_available = False
            return

        try:
            self._state = self._controller.outlet_voltage.get(self._outlet)
            self._attr_available = (
                self._controller.connected
                and self._controller.available
                and self._state is not None
            )
        except Exception as err:
            _LOGGER.error(
                "Error updating outlet %d voltage sensor: %s", self._outlet, err
            )
            self._state = None
            self._attr_available = False


class RacklinkOutletLineFrequency(RacklinkSensor):
    """Outlet line frequency sensor."""

    _attr_has_entity_name = True

    def __init__(self, controller: RacklinkController, outlet: int) -> None:
        """Initialize the outlet line frequency sensor."""
        super().__init__(
            controller,
            f"Outlet {outlet} Line Frequency",
            "Hz",
            f"outlet_{outlet}_line_frequency",
            SensorDeviceClass.FREQUENCY,
            SensorStateClass.MEASUREMENT,
        )
        self._outlet = outlet
        self._attr_entity_registry_enabled_default = True

    async def async_update(self) -> None:
        """Update the sensor state."""
        if not self._controller.connected:
            self._attr_available = False
            return

        try:
            self._state = self._controller.outlet_line_frequency.get(self._outlet)
            self._attr_available = (
                self._controller.connected
                and self._controller.available
                and self._state is not None
            )
        except Exception as err:
            _LOGGER.error(
                "Error updating outlet %d line frequency sensor: %s", self._outlet, err
            )
            self._state = None
            self._attr_available = False
