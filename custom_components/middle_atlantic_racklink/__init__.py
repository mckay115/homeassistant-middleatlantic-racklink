import asyncio
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform
import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from .const import DOMAIN
from .racklink_controller import RacklinkController

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SWITCH, Platform.SENSOR, Platform.BINARY_SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the RackLink component."""
    hass.data[DOMAIN] = {}
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up RackLink from a config entry."""
    controller = RacklinkController(
        hass,
        entry.data["host"],
        entry.data["port"],
        entry.data["username"],
        entry.data["password"],
    )

    await controller.connect()

    hass.data[DOMAIN][entry.entry_id] = controller

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        controller = hass.data[DOMAIN].pop(entry.entry_id)
        await controller.disconnect()

    return unload_ok