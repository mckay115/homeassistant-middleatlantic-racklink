from homeassistant.helpers.entity import Entity

async def async_setup_entry(hass, config_entry, async_add_entities):
    controller = hass.data[DOMAIN][config_entry.entry_id]
    sensors = [
        RacklinkVoltage(controller, "voltage"),
        RacklinkCurrent(controller, "current"),
        RacklinkPower(controller, "power"),
        RacklinkTemperature(controller, "temperature")
    ]
    async_add_entities(sensors)

class RacklinkSensor(Entity):
    def __init__(self, controller, name, unit, sensor_type):
        self._controller = controller
        self._name = name
        self._unit = unit
        self._state = None
        self._sensor_type = sensor_type

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
        super().__init__(controller, "Racklink Voltage", "V", "voltage")

    async def async_update(self):
        self._state = self._controller.sensors.get("voltage")

class RacklinkCurrent(RacklinkSensor):
    def __init__(self, controller):
        super().__init__(controller, "Racklink Current", "A", "current")

    async def async_update(self):
        self._state = self._controller.sensors.get("current")

class RacklinkPower(RacklinkSensor):
    def __init__(self, controller):
        super().__init__(controller, "Racklink Power", "W", "power")

    async def async_update(self):
        self._state = self._controller.sensors.get("power")

class RacklinkTemperature(RacklinkSensor):
    def __init__(self, controller):
        super().__init__(controller, "Racklink Temperature", "°C", "temperature")

    async def async_update(self):
        self._state = self._controller.sensors.get("temperature")