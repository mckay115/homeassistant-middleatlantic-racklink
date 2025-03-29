"""Middle Atlantic RackLink integration for Home Assistant."""

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN
from .racklink_controller import RacklinkController

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SWITCH, Platform.SENSOR, Platform.BINARY_SENSOR]
UPDATE_INTERVAL = timedelta(seconds=10)  # Update every 10 seconds


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Middle Atlantic Racklink from a config entry."""
    _LOGGER.debug(
        "Setting up Middle Atlantic Racklink integration for %s", entry.data["host"]
    )

    controller = RacklinkController(
        entry.data["host"],
        entry.data["port"],
        entry.data["username"],
        entry.data["password"],
    )

    try:
        await controller.connect()
        _LOGGER.info(
            "Connected to Middle Atlantic Racklink at %s (Model: %s, Firmware: %s)",
            entry.data["host"],
            controller.pdu_model,
            controller.pdu_firmware,
        )
    except ValueError as err:
        _LOGGER.error(
            "Failed to connect to Middle Atlantic Racklink at %s: %s",
            entry.data["host"],
            err,
        )
        raise ConfigEntryNotReady(f"Failed to connect: {err}") from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = controller

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def async_update(now=None) -> None:
        """Update the controller data."""
        try:
            await controller.get_all_outlet_states()
            await controller.get_sensor_values()
        except Exception as err:
            _LOGGER.error("Error updating Racklink data: %s", err)

    # Schedule periodic updates
    entry.async_on_unload(
        async_track_time_interval(hass, async_update, UPDATE_INTERVAL)
    )

    # Initial update
    await async_update()

    async def cycle_all_outlets(call: ServiceCall) -> None:
        """Service to cycle all outlets."""
        _LOGGER.info("Cycling all outlets on %s", controller.pdu_name)
        await controller.cycle_all_outlets()

    hass.services.async_register(DOMAIN, "cycle_all_outlets", cycle_all_outlets)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug(
        "Unloading Middle Atlantic Racklink integration for %s", entry.data["host"]
    )

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        controller = hass.data[DOMAIN].pop(entry.entry_id)
        await controller.disconnect()
        _LOGGER.info(
            "Disconnected from Middle Atlantic Racklink at %s", entry.data["host"]
        )

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    _LOGGER.debug(
        "Reloading Middle Atlantic Racklink integration for %s", entry.data["host"]
    )
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
