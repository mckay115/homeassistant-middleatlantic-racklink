from homeassistant.components.switch import SwitchEntity
from .const import DOMAIN

async def async_setup_entry(hass, config_entry, async_add_entities):
    controller = hass.data[DOMAIN][config_entry.entry_id]
    switches = []
    for outlet in range(1, 17):  # Assuming 16 outlets
        switches.append(RacklinkOutlet(controller, outlet))
    switches.append(RacklinkEPOSwitch(controller))
    async_add_entities(switches)

class RacklinkOutlet(SwitchEntity):
    def __init__(self, controller, outlet):
        self._controller = controller
        self._outlet = outlet
        self._name = f"Outlet {outlet}"
        self._state = None

    @property
    def name(self):
        return self._name

    @property
    def is_on(self):
        return self._state

    async def async_turn_on(self, **kwargs):
        await self._controller.set_outlet_state(self._outlet, True)
        self._state = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        await self._controller.set_outlet_state(self._outlet, False)
        self._state = False
        self.async_write_ha_state()

    async def async_update(self):
        self._state = await self._controller.get_outlet_state(self._outlet)

class RacklinkEPOSwitch(SwitchEntity):
    def __init__(self, controller):
        self._controller = controller
        self._name = "Emergency Power Off"
        self._state = False

    @property
    def name(self):
        return self._name

    @property
    def is_on(self):
        return self._state

    async def async_turn_on(self, **kwargs):
        await self._controller.set_epo_state(True)
        self._state = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        await self._controller.set_epo_state(False)
        self._state = False
        self.async_write_ha_state()

    async def async_update(self):
        # EPO state is not directly readable, so we don't update it here
        pass