"""Switch platform for Middle Atlantic Racklink."""
from homeassistant.components.switch import SwitchEntity
from .const import DOMAIN

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Middle Atlantic Racklink switches."""
    controller = hass.data[DOMAIN][config_entry.entry_id]
    switches = []
    for outlet in range(1, 17):
        switches.append(RacklinkOutlet(controller, outlet))
    switches.append(RacklinkAllOn(controller))
    switches.append(RacklinkAllOff(controller))
    async_add_entities(switches)

class RacklinkOutlet(SwitchEntity):
    """Representation of a Racklink outlet."""

    def __init__(self, controller, outlet):
        """Initialize the outlet switch."""
        self._controller = controller
        self._outlet = outlet
        self._name = f"Outlet {outlet}"
        self._state = None

    @property
    def name(self):
        """Return the name of the outlet."""
        return self._name

    @property
    def is_on(self):
        """Return true if outlet is on."""
        return self._state

    async def async_turn_on(self, **kwargs):
        """Turn the outlet on."""
        await self._controller.set_outlet_state(self._outlet, True)
        self._state = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Turn the outlet off."""
        await self._controller.set_outlet_state(self._outlet, False)
        self._state = False
        self.async_write_ha_state()

    async def async_update(self):
        """Fetch new state data for the outlet."""
        self._state = await self._controller.get_outlet_state(self._outlet)

class RacklinkAllOn(SwitchEntity):
    """Switch to turn all outlets on."""

    def __init__(self, controller):
        """Initialize the all on switch."""
        self._controller = controller
        self._name = "All Outlets On"
        self._state = False

    @property
    def name(self):
        """Return the name of the switch."""
        return self._name

    @property
    def is_on(self):
        """Return true if switch is on."""
        return self._state

    async def async_turn_on(self, **kwargs):
        """Turn all outlets on."""
        await self._controller.set_all_outlets(True)
        self._state = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Turn the switch off."""
        self._state = False
        self.async_write_ha_state()

class RacklinkAllOff(SwitchEntity):
    """Switch to turn all outlets off."""

    def __init__(self, controller):
        """Initialize the all off switch."""
        self._controller = controller
        self._name = "All Outlets Off"
        self._state = False

    @property
    def name(self):
        """Return the name of the switch."""
        return self._name

    @property
    def is_on(self):
        """Return true if switch is on."""
        return self._state

    async def async_turn_on(self, **kwargs):
        """Turn all outlets off."""
        await self._controller.set_all_outlets(False)
        self._state = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Turn the switch off."""
        self._state = False
        self.async_write_ha_state()