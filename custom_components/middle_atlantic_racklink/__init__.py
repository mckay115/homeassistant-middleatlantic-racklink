"""Middle Atlantic RackLink integration for Home Assistant."""

from __future__ import annotations

from .const import (
    CONF_CONNECTION_TYPE,
    CONF_ENABLE_VENDOR_FEATURES,
    CONF_SCAN_INTERVAL,
    CONF_USE_HTTPS,
    CONNECTION_TYPE_AUTO,
    CONNECTION_TYPE_REDFISH,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL_REDFISH,
    DEFAULT_SCAN_INTERVAL_TELNET,
    DOMAIN,
)
from .controller.racklink_controller import RacklinkController
from .coordinator import RacklinkCoordinator
from datetime import timedelta
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

import logging

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

RacklinkConfigEntry = ConfigEntry[RacklinkCoordinator]


async def async_setup(_hass: HomeAssistant, _config: ConfigType) -> bool:
    """Set up the Middle Atlantic RackLink component."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: RacklinkConfigEntry) -> bool:
    """Set up Middle Atlantic RackLink from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)
    username = entry.data.get(CONF_USERNAME) or ""
    password = entry.data.get(CONF_PASSWORD) or ""
    connection_type = entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_TYPE_AUTO)

    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL, _default_scan_interval(connection_type)
    )

    _LOGGER.debug(
        "Initializing RackLink controller with host=%s, port=%s, username=%s, "
        "connection_type=%s, scan_interval=%ds",
        host,
        port,
        username,
        connection_type,
        scan_interval,
    )

    controller = RacklinkController(
        host=host,
        port=port,
        username=username,
        password=password,
        connection_type=connection_type,
        use_https=entry.data.get(CONF_USE_HTTPS, True),
        enable_vendor_features=entry.data.get(CONF_ENABLE_VENDOR_FEATURES, True),
    )

    coordinator = RacklinkCoordinator(
        hass=hass,
        config_entry=entry,
        controller=controller,
        update_interval=timedelta(seconds=scan_interval),
    )

    # Raises ConfigEntryNotReady / ConfigEntryAuthFailed on failure
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


def _default_scan_interval(connection_type: str) -> int:
    """Return the default scan interval for a connection type."""
    if connection_type == CONNECTION_TYPE_REDFISH:
        return DEFAULT_SCAN_INTERVAL_REDFISH
    return DEFAULT_SCAN_INTERVAL_TELNET


async def async_update_options(
    _hass: HomeAssistant, entry: RacklinkConfigEntry
) -> None:
    """Apply changed options to the running coordinator."""
    coordinator = entry.runtime_data
    connection_type = entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_TYPE_AUTO)
    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL, _default_scan_interval(connection_type)
    )
    new_interval = timedelta(seconds=scan_interval)
    if coordinator.update_interval != new_interval:
        _LOGGER.debug("Updating scan interval to %ds", scan_interval)
        coordinator.update_interval = new_interval


async def async_unload_entry(hass: HomeAssistant, entry: RacklinkConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        await entry.runtime_data.controller.disconnect()

    return unload_ok


async def async_migrate_entry(_hass: HomeAssistant, entry: RacklinkConfigEntry) -> bool:
    """Migrate an old config entry to the new version."""
    _LOGGER.debug("Migrating from version %s", entry.version)
    return True
