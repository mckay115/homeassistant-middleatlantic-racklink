"""Middle Atlantic RackLink integration for Home Assistant."""

import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, DEFAULT_PORT
from .device import RacklinkDevice
from .coordinator import RacklinkDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SWITCH,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the Middle Atlantic Racklink integration from a config entry."""
    host = entry.data["host"]
    port = int(entry.data.get("port", DEFAULT_PORT))
    username = entry.data["username"]
    password = entry.data["password"]
    model = entry.data.get("model", "AUTO_DETECT")

    _LOGGER.info(
        "Setting up Middle Atlantic Racklink integration for %s:%s (%s)",
        host,
        port,
        model,
    )

    # Create device instance
    device = RacklinkDevice(host, port, username, password, model)

    # Start background connection with timeout
    try:
        # Attempt initial connection to device with timeout
        _LOGGER.debug("Starting background connection to %s", host)
        connection_task = device.start_background_connection()
        # Set 15 second timeout for initial connection
        try:
            await asyncio.wait_for(connection_task, timeout=15)
        except asyncio.TimeoutError:
            _LOGGER.warning("Initial connection timed out, proceeding with setup")
            # Continue anyway - entities will show as unavailable until connected

        # Wait briefly for connection to establish - reduce from 5 to 2 seconds
        await asyncio.sleep(2)

        # Force an initial update to populate data with timeout
        _LOGGER.debug("Triggering initial data refresh for %s", host)
        try:
            await asyncio.wait_for(device.update(), timeout=10)
        except asyncio.TimeoutError:
            _LOGGER.warning("Initial data refresh timed out")
            # Continue with setup - the coordinator will retry

    except Exception as e:
        _LOGGER.error("Error during initial setup: %s", e)
        # Continue anyway - Home Assistant will show entities as unavailable
        # until connection is established

    # Store the device
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = device

    # Create update coordinator to refresh the data
    coordinator = RacklinkDataUpdateCoordinator(
        hass, device, f"Racklink {host}", DEFAULT_SCAN_INTERVAL
    )

    # Store the coordinator
    hass.data[DOMAIN][entry.entry_id + "_coordinator"] = coordinator

    # Initial data refresh
    await coordinator.async_config_entry_first_refresh()

    # Register services
    async def async_cycle_all_outlets(call):
        """Cycle power to all outlets."""
        # Make sure this doesn't fail even if the device is unavailable
        try:
            _LOGGER.info("Cycling power to all outlets")
            await device.cycle_all_outlets()
            await coordinator.async_refresh()
        except Exception as e:
            _LOGGER.error("Error cycling all outlets: %s", e)

    async def async_cycle_outlet(call):
        """Cycle power to a specific outlet."""
        outlet_number = call.data.get("outlet_number")
        if not outlet_number:
            _LOGGER.error("Outlet number is required for cycle_outlet service")
            return

        # Make sure this doesn't fail even if the device is unavailable
        try:
            _LOGGER.info("Cycling power to outlet %s", outlet_number)
            await device.cycle_outlet(outlet_number)
            await coordinator.async_refresh()
        except Exception as e:
            _LOGGER.error("Error cycling outlet %s: %s", outlet_number, e)

    async def async_set_outlet_name(call):
        """Set the name of a specific outlet."""
        outlet_number = call.data.get("outlet_number")
        name = call.data.get("name")
        if not outlet_number or not name:
            _LOGGER.error(
                "Outlet number and name are required for set_outlet_name service"
            )
            return

        # Make sure this doesn't fail even if the device is unavailable
        try:
            _LOGGER.info("Setting outlet %s name to %s", outlet_number, name)
            await device.set_outlet_name(outlet_number, name)
            await coordinator.async_refresh()
        except Exception as e:
            _LOGGER.error("Error setting outlet %s name: %s", outlet_number, e)

    async def async_set_pdu_name(call):
        """Set the name of the PDU."""
        name = call.data.get("name")
        if not name:
            _LOGGER.error("Name is required for set_pdu_name service")
            return

        # Make sure this doesn't fail even if the device is unavailable
        try:
            _LOGGER.info("Setting PDU name to %s", name)
            await device.set_pdu_name(name)
            await coordinator.async_refresh()
        except Exception as e:
            _LOGGER.error("Error setting PDU name: %s", e)

    # Register services with Home Assistant
    hass.services.async_register(DOMAIN, "cycle_all_outlets", async_cycle_all_outlets)
    hass.services.async_register(DOMAIN, "cycle_outlet", async_cycle_outlet)
    hass.services.async_register(DOMAIN, "set_outlet_name", async_set_outlet_name)
    hass.services.async_register(DOMAIN, "set_pdu_name", async_set_pdu_name)

    # Register shutdown function
    async def async_stop_controller(event):
        """Stop the controller when Home Assistant stops."""
        _LOGGER.debug("Shutting down Middle Atlantic Racklink controller")

        try:
            await device.shutdown()
        except Exception as e:
            _LOGGER.error("Error stopping controller: %s", e)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, async_stop_controller)

    # Set up all the platform entities
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Shutdown device gracefully
    if unload_ok and entry.entry_id in hass.data[DOMAIN]:
        device = hass.data[DOMAIN][entry.entry_id]

        try:
            await device.shutdown()
        except Exception as e:
            _LOGGER.error("Error shutting down device: %s", e)

        # Remove entry from hass.data
        hass.data[DOMAIN].pop(entry.entry_id)
        if entry.entry_id + "_coordinator" in hass.data[DOMAIN]:
            hass.data[DOMAIN].pop(entry.entry_id + "_coordinator")

        # If domain data is empty, remove the domain
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
