"""Middle Atlantic RackLink integration for Home Assistant."""

# Standard library imports
from .const import (
    CONNECTION_TYPE_REDFISH,
    DEFAULT_SCAN_INTERVAL_REDFISH,
    DEFAULT_SCAN_INTERVAL_TELNET,
    DOMAIN,
)

# Local application/library specific imports
from .controller.racklink_controller import RacklinkController
from .coordinator import RacklinkCoordinator
from datetime import timedelta

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
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType
from typing import Any, Dict

import logging

# Third-party imports
import voluptuous as vol

_LOGGER = logging.getLogger(__name__)

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
    connection_type = entry.data.get("connection_type", "auto")

    # Smart default scan interval based on connection type
    if connection_type == CONNECTION_TYPE_REDFISH:
        default_interval = DEFAULT_SCAN_INTERVAL_REDFISH
    else:
        default_interval = DEFAULT_SCAN_INTERVAL_TELNET

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, default_interval)

    # Create controller instance
    _LOGGER.debug(
        "Initializing RackLink controller with host=%s, port=%s, username=%s, connection_type=%s",
        host,
        port,
        username,
        connection_type,
    )
    _LOGGER.info(
        "Using %d second update interval for %s connection",
        scan_interval,
        connection_type,
    )
    controller = RacklinkController(
        host=host,
        port=port,
        username=username,
        password=password,
        connection_type=connection_type,
        use_https=entry.data.get("use_https", True),
        enable_vendor_features=entry.data.get("enable_vendor_features", True),
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

    # Migrate legacy outlet-metric unique IDs now that the serial is known
    await _async_migrate_outlet_metric_unique_ids(
        hass, entry, controller.pdu_serial
    )

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


# Suffixes of the outlet-metric sensors that historically used a
# never-populated "device_id" and collapsed to the literal "unknown_" prefix.
_LEGACY_OUTLET_METRIC_PREFIX = "unknown_"
_OUTLET_METRIC_SUFFIXES = ("_power", "_energy", "_current", "_voltage")


async def _async_migrate_outlet_metric_unique_ids(
    hass: HomeAssistant, entry: ConfigEntry, serial: str | None
) -> None:
    """Rename legacy ``unknown_<outlet>_<metric>`` unique IDs to use the serial.

    Earlier versions built the outlet power/energy/current/voltage unique IDs
    from a ``device_id`` key that was never set, so they all collapsed to
    ``unknown_<outlet>_<metric>``. With more than one PDU these collided and the
    second PDU's entities were dropped. The IDs are now namespaced by
    ``pdu_serial``; rename any pre-existing entities on this entry so their
    history carries over instead of orphaning them.
    """
    if not serial or serial == "unknown":
        # Without a usable serial the new ID would be no better than the old one.
        return

    registry = er.async_get(hass)

    @callback
    def _migrate(entity: er.RegistryEntry) -> dict[str, str] | None:
        old_unique_id = entity.unique_id
        if not old_unique_id.startswith(_LEGACY_OUTLET_METRIC_PREFIX):
            return None
        if not old_unique_id.endswith(_OUTLET_METRIC_SUFFIXES):
            return None

        # "unknown_7_voltage" -> "<serial>_7_voltage"
        suffix = old_unique_id[len(_LEGACY_OUTLET_METRIC_PREFIX) :]
        new_unique_id = f"{serial}_{suffix}"
        if new_unique_id == old_unique_id:
            return None

        # Don't clobber an entity that already owns the target unique ID.
        if registry.async_get_entity_id(
            entity.domain, entity.platform, new_unique_id
        ):
            _LOGGER.warning(
                "Cannot migrate unique_id %s to %s; target already exists",
                old_unique_id,
                new_unique_id,
            )
            return None

        _LOGGER.info(
            "Migrating outlet metric unique_id %s -> %s",
            old_unique_id,
            new_unique_id,
        )
        return {"new_unique_id": new_unique_id}

    await er.async_migrate_entries(hass, entry.entry_id, _migrate)


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
