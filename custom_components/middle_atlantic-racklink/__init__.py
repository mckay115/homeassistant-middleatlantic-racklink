import asyncio
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .const import DOMAIN
from .racklink_controller import RacklinkController

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["switch", "sensor", "binary_sensor"]

async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the RackLink component."""
    hass.data[DOMAIN] = {}
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
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

    for platform in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(entry, platform)
        )

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, platform)
                for platform in PLATFORMS
            ]
        )
    )

    if unload_ok:
        controller = hass.data[DOMAIN].pop(entry.entry_id)
        await controller.disconnect()

    return unload_ok