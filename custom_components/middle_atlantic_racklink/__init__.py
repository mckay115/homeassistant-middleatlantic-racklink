from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN
from .racklink_controller import RacklinkController

PLATFORMS: list[Platform] = [Platform.SWITCH, Platform.SENSOR, Platform.BINARY_SENSOR]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Middle Atlantic Racklink from a config entry."""
    controller = RacklinkController(
        hass,
        entry.data["host"],
        entry.data["port"],
        entry.data["username"],
        entry.data["password"],
    )

    try:
        await controller.connect()
    except ValueError as err:
        raise ConfigEntryNotReady(f"Failed to connect: {err}") from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = controller

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        controller = hass.data[DOMAIN].pop(entry.entry_id)
        await controller.disconnect()

    return unload_ok