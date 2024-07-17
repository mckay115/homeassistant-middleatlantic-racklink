"""Binary sensor platform for Middle Atlantic Racklink."""
from homeassistant.components.binary_sensor import BinarySensorEntity
from .const import DOMAIN

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Middle Atlantic Racklink binary sensors."""
    controller = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([RacklinkSurgeProtection(controller)])

class RacklinkSurgeProtection(BinarySensorEntity):
    """Representation of a Racklink surge protection sensor."""

    def __init__(self, controller):
        """Initialize the surge protection sensor."""
        self._controller = controller
        self._state = None

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Racklink Surge Protection"

    @property
    def is_on(self):
        """Return true if surge protection is active."""
        return self._state

    @property
    def device_class(self):
        """Return the class of this device."""
        return "safety"

    async def async_update(self):
        """Fetch new state data for the sensor."""
        self._state = await self._controller.get_sensor_value(0x59)