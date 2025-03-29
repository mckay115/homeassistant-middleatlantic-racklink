from homeassistant.components.switch import SwitchEntity
from homeassistant.core import ServiceCall
from .const import DOMAIN


async def async_setup_entry(hass, config_entry, async_add_entities):
    controller = hass.data[DOMAIN][config_entry.entry_id]
    switches = []
    for outlet in range(1, 9):  # Assuming 8 outlets, adjust as needed
        switches.append(RacklinkOutlet(controller, outlet))
    switches.append(RacklinkAllOn(controller))
    switches.append(RacklinkAllOff(controller))
    async_add_entities(switches)

    async def cycle_all_outlets(call: ServiceCall):
        await controller.cycle_all_outlets()

    hass.services.async_register(DOMAIN, "cycle_all_outlets", cycle_all_outlets)


class RacklinkOutlet(SwitchEntity):
    def __init__(self, controller, outlet):
        self._controller = controller
        self._outlet = outlet
        self._name = f"Outlet {outlet}"
        self._state = None
        self._unique_id = f"{controller.pdu_serial}_outlet_{outlet}"

    @property
    def name(self):
        return self._name

    @property
    def is_on(self):
        return self._state

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._controller.pdu_serial)},
            "name": f"Racklink PDU {self._controller.pdu_name}",
            "manufacturer": "Middle Atlantic",
            "model": "Racklink PDU",
            "sw_version": self._controller.pdu_firmware,
        }

    async def async_turn_on(self, **kwargs):
        await self._controller.set_outlet_state(self._outlet, True)
        self._state = True

    async def async_turn_off(self, **kwargs):
        await self._controller.set_outlet_state(self._outlet, False)
        self._state = False

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await self.async_update()

    async def async_update(self):
        """Fetch new state data for this outlet."""
        await self._controller.get_all_outlet_states()
        self._state = self._controller.outlet_states.get(self._outlet, False)
        self._name = self._controller.outlet_names.get(
            self._outlet, f"Outlet {self._outlet}"
        )

    async def async_cycle(self):
        await self._controller.cycle_outlet(self._outlet)

    @property
    def extra_state_attributes(self):
        return {
            "can_cycle": True,
            "power": self._controller.outlet_power.get(self._outlet),
            "current": self._controller.outlet_current.get(self._outlet),
        }


class RacklinkAllOn(SwitchEntity):
    def __init__(self, controller):
        self._controller = controller
        self._name = "All Outlets On"
        self._state = False
        self._unique_id = f"{controller.pdu_serial}_all_on"

    @property
    def name(self):
        return self._name

    @property
    def is_on(self):
        return self._state

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._controller.pdu_serial)},
            "name": f"Racklink PDU {self._controller.pdu_name}",
            "manufacturer": "Middle Atlantic",
            "model": "Racklink PDU",
            "sw_version": self._controller.pdu_firmware,
        }

    async def async_turn_on(self, **kwargs):
        await self._controller.set_all_outlets(True)
        self._state = True

    async def async_turn_off(self, **kwargs):
        self._state = False


class RacklinkAllOff(SwitchEntity):
    def __init__(self, controller):
        self._controller = controller
        self._name = "All Outlets Off"
        self._state = False
        self._unique_id = f"{controller.pdu_serial}_all_off"

    @property
    def name(self):
        return self._name

    @property
    def is_on(self):
        return self._state

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._controller.pdu_serial)},
            "name": f"Racklink PDU {self._controller.pdu_name}",
            "manufacturer": "Middle Atlantic",
            "model": "Racklink PDU",
            "sw_version": self._controller.pdu_firmware,
        }

    async def async_turn_on(self, **kwargs):
        await self._controller.set_all_outlets(False)
        self._state = True

    async def async_turn_off(self, **kwargs):
        self._state = False
