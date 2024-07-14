from homeassistant.helpers.entity import Entity
from .const import DOMAIN

SENSOR_TYPES = {
    0x52: {"name": "Voltage", "unit": "V"},
    0x54: {"name": "Current", "unit": "A"},
    0x56: {"name": "Power", "unit": "W"},
    0x55: {"name": "Temperature", "unit": "°F"},
}

async def async_setup_entry(hass, config_entry, async_add_entities):
    controller = hass.data[DOMAIN][config_entry.entry_id]
    sensors = []
    for sensor_type, sensor_info in SENSOR_TYPES.items():
        sensors.append(RacklinkSensor(controller, sensor_type, sensor_info))
    async_add_entities(sensors)

class RacklinkSensor(Entity):
    def __init__(self, controller, sensor_type, sensor_info):
        self._controller = controller
        self._sensor_type = sensor_type
        self._name = sensor_info["name"]
        self._unit = sensor_info["unit"]
        self._state = None

    @property
    def name(self):
        return f"RackLink {self._name}"

    @property
    def state(self):
        return self._state

    @property
    def unit_of_measurement(self):
        return self._unit

    async def async_update(self):
        self._state = await self._controller.get_sensor_value(self._sensor_type)