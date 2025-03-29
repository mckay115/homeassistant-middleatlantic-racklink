"""Binary sensor platform for Middle Atlantic Racklink."""

import logging
from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.helpers.entity import DeviceInfo

from .const import ATTR_MANUFACTURER, ATTR_MODEL, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Middle Atlantic Racklink binary sensors."""
    controller = hass.data[DOMAIN][config_entry.entry_id]

    # Only add surge protection sensor if the model supports it
    capabilities = controller.get_model_capabilities()

    if capabilities.get("has_surge_protection", False):
        _LOGGER.info(
            "Setting up surge protection sensor for %s (%s)",
            controller.pdu_name,
            controller.pdu_model,
        )
        async_add_entities([RacklinkSurgeProtection(controller)])
    else:
        _LOGGER.debug(
            "Skipping surge protection sensor for %s, not supported by model %s",
            controller.pdu_name,
            controller.pdu_model,
        )


class RacklinkSurgeProtection(BinarySensorEntity):
    """Representation of a Racklink surge protection sensor."""

    def __init__(self, controller):
        """Initialize the surge protection sensor."""
        self._controller = controller
        self._state = None
        self._attr_name = "Surge Protection"
        self._attr_unique_id = f"{controller.pdu_serial}_surge_protection"
        self._attr_device_class = BinarySensorDeviceClass.SAFETY
        self._attr_available = False

    @property
    def is_on(self):
        """Return true if surge protection is active."""
        return self._state

    @property
    def available(self):
        """Return if sensor is available."""
        return self._controller.connected and self._controller.available

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

    async def async_update(self):
        """Update the surge protection status."""
        if not self._controller.connected:
            self._attr_available = False
            return

        try:
            self._state = await self._controller.get_surge_protection_status()
            self._attr_available = (
                self._controller.connected and self._controller.available
            )
        except Exception as err:
            _LOGGER.error("Error updating surge protection status: %s", err)
            self._state = None
            self._attr_available = False
