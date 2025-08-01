"""Middle Atlantic RackLink integration for Home Assistant."""

# Standard library imports
from datetime import timedelta
import logging
from typing import Any, Dict

# Third-party imports
import voluptuous as vol

# Home Assistant core imports
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
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

# Local application/library specific imports
from .controller.racklink_controller import RacklinkController
from .coordinator import RacklinkCoordinator

_LOGGER = logging.getLogger(__name__)

DOMAIN = "middle_atlantic_racklink"
DEFAULT_SCAN_INTERVAL = 30

PLATFORMS = [
    Platform.SWITCH,
    Platform.SENSOR,
    Platform.BUTTON,
    Platform.BINARY_SENSOR,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(_hass: HomeAssistant, _config: ConfigType) -> bool:
    """Set up the Middle Atlantic RackLink component from YAML."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Middle Atlantic RackLink from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, 60000)
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
        update_interval=timedelta(seconds=scan_interval),
    )

    # Perform initial data update with proper error handling
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        # Convert specific errors to Home Assistant standard exceptions
        error_msg = str(err).lower()
        if any(
            auth_error in error_msg
            for auth_error in ["auth", "credential", "password", "username", "login"]
        ):
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
        elif any(
            conn_error in error_msg
            for conn_error in ["connect", "timeout", "network", "unreachable"]
        ):
            raise ConfigEntryError(f"Cannot connect to device: {err}") from err
        else:
            raise ConfigEntryError(f"Setup failed: {err}") from err

    # Store controller and coordinator in hass.data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register update listener for config entry changes
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    # Register service for testing direct commands
    async def test_direct_commands_service(_call):
        """Service to test direct commands."""
        _LOGGER.info("Service call: test_direct_commands")
        results = await coordinator.test_direct_commands()
        _LOGGER.info("Direct command test results:\n%s", results)

    hass.services.async_register(
        DOMAIN, "test_direct_commands", test_direct_commands_service
    )

    return True


async def async_update_options(
    home_assistant: HomeAssistant, entry: ConfigEntry
) -> None:  # pylint: disable=unused-argument
    """Update options when config entry options are changed."""
    await home_assistant.config_entries.async_reload(entry.entry_id)


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


async def async_migrate_entry(_hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate an old config entry to the new version."""
    _LOGGER.debug("Migrating from version %s", entry.version)

    # Perform migrations if needed (none needed yet)

    return True
