from homeassistant.helpers.entity import Entity
from .const import DOMAIN

SENSOR_TYPES = {
    0x50: {"name": "Kilowatt Hours", "unit": "kWh", "icon": "mdi:power-plug"},
    0x51: {"name": "Peak Voltage", "unit": "V", "icon": "mdi:flash"},
    0x52: {"name": "RMS Voltage", "unit": "V", "icon": "mdi:flash"},
    0x53: {"name": "Peak Load", "unit": "A", "icon": "mdi:current-ac"},
    0x54: {"name": "RMS Load", "unit": "A", "icon": "mdi:current-ac"},
    0x55: {"name": "Temperature", "unit": "°F", "icon": "mdi:thermometer"},
    0x56: {"name": "Wattage", "unit": "W", "icon": "mdi:power-plug"},
    0x57: {"name": "Power Factor", "unit": None, "icon": "mdi:angle-acute"},
    0x58: {"name": "Thermal Load", "unit": "BTU", "icon": "mdi:radiator"},
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
        self._icon = sensor_info["icon"]
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

    @property
    def icon(self):
        return self._icon

    async def async_update(self):
        self._state = await self._controller.get_sensor_value(self._sensor_type)