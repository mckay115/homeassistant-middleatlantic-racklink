from homeassistant.components.binary_sensor import BinarySensorEntity
from .const import DOMAIN

async def async_setup_entry(hass, config_entry, async_add_entities):
    controller = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([RacklinkSurgeProtection(controller)])

class RacklinkSurgeProtection(BinarySensorEntity):
    def __init__(self, controller):
        self._controller = controller
        self._state = None

    @property
    def name(self):
        return "RackLink Surge Protection"

    @property
    def is_on(self):
        return self._state

    @property
    def device_class(self):
        return "safety"

    async def async_update(self):
        await self._controller.get_sensor_value(0x59)
        self._state = self._controller.sensors.get("surge_protection") == 0x01