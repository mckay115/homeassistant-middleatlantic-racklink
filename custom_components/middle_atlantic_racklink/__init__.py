"""Middle Atlantic RackLink integration for Home Assistant."""

import logging
from typing import Any, Dict

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .controller.racklink_controller import RacklinkController
from .coordinator import RacklinkCoordinator

_LOGGER = logging.getLogger(__name__)

DOMAIN = "middle_atlantic_racklink"
DEFAULT_SCAN_INTERVAL = 10

PLATFORMS = [
    Platform.SWITCH,
    Platform.SENSOR,
    Platform.BUTTON,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Middle Atlantic RackLink component from YAML."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Middle Atlantic RackLink from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, 6000)
    username = entry.data.get(CONF_USERNAME)
    password = entry.data.get(CONF_PASSWORD)
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    # Create controller instance
    _LOGGER.debug(
        "Initializing RackLink controller with host=%s, port=%s, username=%s",
        host,
        port,
        username,
    )
    controller = RacklinkController(
        host=host,
        port=port,
        username=username,
        password=password,
    )

    # Create and initialize coordinator
    coordinator = RacklinkCoordinator(
        hass=hass,
        controller=controller,
        scan_interval=scan_interval,
    )

    # Perform initial data update
    await coordinator.async_config_entry_first_refresh()

    # Store controller and coordinator in hass.data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register update listener for config entry changes
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    # Register service for testing direct commands
    async def test_direct_commands_service(call):
        """Service to test direct commands."""
        _LOGGER.info("Service call: test_direct_commands")
        results = await coordinator.test_direct_commands()
        _LOGGER.info("Direct command test results:\n%s", results)

    hass.services.async_register(
        DOMAIN, "test_direct_commands", test_direct_commands_service
    )

    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update options when config entry options are changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # Disconnect from controller
        coordinator = hass.data[DOMAIN][entry.entry_id]
        await coordinator.controller.disconnect()

        # Remove coordinator from hass.data
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate an old config entry to the new version."""
    _LOGGER.debug("Migrating from version %s", entry.version)

    # Perform migrations if needed (none needed yet)

    return True
