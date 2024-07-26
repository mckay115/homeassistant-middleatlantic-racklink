from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_track_time_interval

from datetime import timedelta
from .const import DOMAIN
from .racklink_controller import RacklinkController

PLATFORMS: list[Platform] = [Platform.SWITCH, Platform.SENSOR, Platform.BINARY_SENSOR]
UPDATE_INTERVAL = timedelta(seconds=10)  # Update every 10 seconds

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Middle Atlantic Racklink from a config entry."""
    controller = RacklinkController(
        entry.data["host"],
        entry.data["port"],
        entry.data["username"],
        entry.data["password"]
    )

    try:
        await controller.connect()
    except ValueError as err:
        raise ConfigEntryNotReady(f"Failed to connect: {err}") from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = controller

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def async_update(now=None):
        """Update the controller data."""
        try:
            await controller.get_all_outlet_states()
            await controller.get_sensor_values()
            await hass.helpers.entity_component.async_update_entity(DOMAIN)
        except Exception as err:
            _LOGGER.error(f"Error updating Racklink data: {err}")

    # Schedule periodic updates
    entry.async_on_unload(
        async_track_time_interval(hass, async_update, UPDATE_INTERVAL)
    )

    # Initial update
    await async_update()

    def cycle_all_outlets(call):
        hass.async_create_task(controller.cycle_all_outlets())

    hass.services.async_register(DOMAIN, "cycle_all_outlets", cycle_all_outlets)

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        controller = hass.data[DOMAIN].pop(entry.entry_id)
        await controller.disconnect()

    return unload_ok