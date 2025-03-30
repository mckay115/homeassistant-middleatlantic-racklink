"""Button platform for Middle Atlantic Racklink."""

import asyncio
import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ATTR_MANUFACTURER, ATTR_MODEL, DOMAIN
from .racklink_controller import RacklinkController

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Middle Atlantic Racklink buttons from config entry."""
    controller = hass.data[DOMAIN][config_entry.entry_id]

    # Add buttons for all on/off control
    buttons = [
        RacklinkAllOnButton(controller),
        RacklinkAllOffButton(controller),
        RacklinkCycleAllButton(controller),
    ]

    # Add individual cycle buttons for each outlet
    capabilities = controller.get_model_capabilities()
    outlet_count = capabilities.get("num_outlets", 8)  # Default to 8 if not determined

    for outlet in range(1, outlet_count + 1):
        buttons.append(RacklinkOutletCycleButton(controller, outlet))

    async_add_entities(buttons)


class RacklinkButtonBase(ButtonEntity):
    """Base class for Racklink button entities."""

    def __init__(
        self, controller: RacklinkController, name: str, unique_id_suffix: str
    ) -> None:
        """Initialize the button."""
        self._controller = controller
        self._attr_name = name
        self._attr_unique_id = f"{controller.pdu_serial}_{unique_id_suffix}"
        self._attr_available = False

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this entity."""
        return {
            "identifiers": {(DOMAIN, self._controller.pdu_serial)},
            "name": f"Racklink PDU {self._controller.pdu_name}",
            "manufacturer": ATTR_MANUFACTURER,
            "model": self._controller.pdu_model or ATTR_MODEL,
            "sw_version": self._controller.pdu_firmware,
        }

    @property
    def available(self) -> bool:
        """Return if button is available."""
        return self._controller.connected and self._controller.available

    async def async_update(self) -> None:
        """Update availability."""
        self._attr_available = self._controller.connected and self._controller.available


class RacklinkAllOnButton(RacklinkButtonBase):
    """Button to turn all outlets on."""

    def __init__(self, controller: RacklinkController) -> None:
        """Initialize the all on button."""
        super().__init__(controller, "All Outlets On", "all_on_button")

    async def async_press(self) -> None:
        """Handle the button press. Turn all outlets on."""
        try:
            _LOGGER.debug("Turning on all outlets")
            await asyncio.wait_for(self._controller.set_all_outlets(True), timeout=10)
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout turning on all outlets")
        except Exception as err:
            _LOGGER.error("Error turning on all outlets: %s", err)


class RacklinkAllOffButton(RacklinkButtonBase):
    """Button to turn all outlets off."""

    def __init__(self, controller: RacklinkController) -> None:
        """Initialize the all off button."""
        super().__init__(controller, "All Outlets Off", "all_off_button")

    async def async_press(self) -> None:
        """Handle the button press. Turn all outlets off."""
        try:
            _LOGGER.debug("Turning off all outlets")
            await asyncio.wait_for(self._controller.set_all_outlets(False), timeout=10)
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout turning off all outlets")
        except Exception as err:
            _LOGGER.error("Error turning off all outlets: %s", err)


class RacklinkCycleAllButton(RacklinkButtonBase):
    """Button to cycle all outlets."""

    def __init__(self, controller: RacklinkController) -> None:
        """Initialize the cycle all button."""
        super().__init__(controller, "Cycle All Outlets", "cycle_all_button")

    async def async_press(self) -> None:
        """Handle the button press. Cycle all outlets."""
        try:
            _LOGGER.debug("Cycling all outlets")
            await asyncio.wait_for(self._controller.cycle_all_outlets(), timeout=10)
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout cycling all outlets")
        except Exception as err:
            _LOGGER.error("Error cycling all outlets: %s", err)


class RacklinkOutletCycleButton(RacklinkButtonBase):
    """Button to cycle a specific outlet."""

    _attr_has_entity_name = True
    entity_category = None

    def __init__(self, controller: RacklinkController, outlet: int) -> None:
        """Initialize the outlet cycle button."""
        super().__init__(
            controller, f"Outlet {outlet} Cycle", f"cycle_outlet_{outlet}_button"
        )
        self._outlet = outlet
        outlet_name = controller.outlet_names.get(outlet, f"Outlet {outlet}")
        self._attr_entity_registry_enabled_default = True

    async def async_press(self) -> None:
        """Handle the button press. Cycle the outlet."""
        try:
            _LOGGER.debug("Cycling outlet %d", self._outlet)
            await asyncio.wait_for(
                self._controller.cycle_outlet(self._outlet), timeout=10
            )
            # Force a controller update after cycling
            await asyncio.sleep(3)
            await self._controller.update()
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout cycling outlet %d", self._outlet)
        except Exception as err:
            _LOGGER.error("Error cycling outlet %d: %s", self._outlet, err)

    async def async_added_to_hass(self) -> None:
        """Register callbacks when entity is added."""
        # Listen for entity updates from the dispatcher signal
        self.async_on_remove(
            self.hass.helpers.dispatcher.async_dispatcher_connect(
                f"{DOMAIN}_entity_update", self.async_write_ha_state
            )
        )
