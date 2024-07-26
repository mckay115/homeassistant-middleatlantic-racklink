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

async def async_setup_entry(hass, config_entry, async_add_entities):
    controller = hass.data[DOMAIN][config_entry.entry_id]
    sensors = [
        RacklinkVoltage(controller),
        RacklinkCurrent(controller),
        RacklinkPower(controller),
        RacklinkEnergy(controller),
        RacklinkTemperature(controller)
    ]
    for i in range(1, 9):  # Assuming 8 outlets, adjust as needed
        sensors.extend([
            RacklinkOutletPower(controller, i),
            RacklinkOutletCurrent(controller, i),
            RacklinkOutletEnergy(controller, i)
        ])
    async_add_entities(sensors)

class RacklinkSensor(Entity):
    def __init__(self, controller, name, unit, sensor_type, device_class, state_class):
        self._controller = controller
        self._name = name
        self._unit = unit
        self._state = None
        self._sensor_type = sensor_type
        self._device_class = device_class
        self._state_class = state_class

    @property
    def name(self):
        return self._name

    @property
    def state(self):
        return self._state

    @property
    def unit_of_measurement(self):
        return self._unit

    @property
    def device_class(self):
        return self._device_class

    @property
    def state_class(self):
        return self._state_class

    @property
    def unique_id(self):
        return f"{self._controller.pdu_serial}_{self._sensor_type}"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._controller.pdu_serial)},
            "name": f"Racklink PDU {self._controller.pdu_name}",
            "manufacturer": "Middle Atlantic",
            "model": "Racklink PDU",
            "sw_version": self._controller.pdu_firmware,
        }

class RacklinkVoltage(RacklinkSensor):
    def __init__(self, controller):
        super().__init__(controller, "Racklink Voltage", UnitOfElectricPotential.VOLT, "voltage", 
                         SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT)

    async def async_update(self):
        self._state = self._controller.sensors.get("voltage")

class RacklinkCurrent(RacklinkSensor):
    def __init__(self, controller):
        super().__init__(controller, "Racklink Current", UnitOfElectricCurrent.AMPERE, "current", 
                         SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT)

    async def async_update(self):
        self._state = self._controller.sensors.get("current")

class RacklinkPower(RacklinkSensor):
    def __init__(self, controller):
        super().__init__(controller, "Racklink Power", UnitOfPower.WATT, "power", 
                         SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT)

    async def async_update(self):
        self._state = self._controller.sensors.get("power")

class RacklinkEnergy(RacklinkSensor):
    def __init__(self, controller):
        super().__init__(controller, "Racklink Energy", UnitOfEnergy.KILO_WATT_HOUR, "energy", 
                         SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING)

    async def async_update(self):
        self._state = self._controller.sensors.get("energy")

class RacklinkTemperature(RacklinkSensor):
    def __init__(self, controller):
        super().__init__(controller, "Racklink Temperature", UnitOfTemperature.CELSIUS, "temperature", 
                         SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT)

    async def async_update(self):
        self._state = self._controller.sensors.get("temperature")

class RacklinkOutletPower(RacklinkSensor):
    def __init__(self, controller, outlet):
        super().__init__(controller, f"Outlet {outlet} Power", UnitOfPower.WATT, f"outlet_{outlet}_power", 
                         SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT)
        self._outlet = outlet

    async def async_update(self):
        self._state = self._controller.outlet_power.get(self._outlet)

class RacklinkOutletCurrent(RacklinkSensor):
    def __init__(self, controller, outlet):
        super().__init__(controller, f"Outlet {outlet} Current", UnitOfElectricCurrent.AMPERE, f"outlet_{outlet}_current", 
                         SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT)
        self._outlet = outlet

    async def async_update(self):
        self._state = self._controller.outlet_current.get(self._outlet)

class RacklinkOutletEnergy(RacklinkSensor):
    def __init__(self, controller, outlet):
        super().__init__(controller, f"Outlet {outlet} Energy", UnitOfEnergy.KILO_WATT_HOUR, f"outlet_{outlet}_energy", 
                         SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING)
        self._outlet = outlet

    async def async_update(self):
        self._state = self._controller.outlet_energy.get(self._outlet)

    @property
    def last_reset(self):
        return None  # or return a specific datetime if you have this information