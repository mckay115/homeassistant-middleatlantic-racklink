"""Sensor platform for Middle Atlantic Racklink."""
from homeassistant.helpers.entity import Entity
from .const import DOMAIN

SENSOR_TYPES = {
    0x52: {"name": "RMS Voltage", "unit": "V", "icon": "mdi:flash"},
    0x54: {"name": "RMS Current", "unit": "A", "icon": "mdi:current-ac"},
    0x56: {"name": "Power", "unit": "W", "icon": "mdi:power-plug"},
    0x55: {"name": "Temperature", "unit": "°F", "icon": "mdi:thermometer"},
}

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Middle Atlantic Racklink sensors."""
    controller = hass.data[DOMAIN][config_entry.entry_id]
    sensors = []
    for sensor_type, sensor_info in SENSOR_TYPES.items():
        sensors.append(RacklinkSensor(controller, sensor_type, sensor_info))
    async_add_entities(sensors)

class RacklinkSensor(Entity):
    """Representation of a Racklink sensor."""

    def __init__(self, controller, sensor_type, sensor_info):
        """Initialize the sensor."""
        self._controller = controller
        self._sensor_type = sensor_type
        self._name = sensor_info["name"]
        self._unit = sensor_info["unit"]
        self._icon = sensor_info["icon"]
        self._state = None

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"Racklink {self._name}"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return self._unit

    @property
    def icon(self):
        """Return the icon of the sensor."""
        return self._icon

    async def async_update(self):
        """Fetch new state data for the sensor."""
        self._state = await self._controller.get_sensor_value(self._sensor_type)