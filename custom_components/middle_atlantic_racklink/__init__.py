"""Middle Atlantic RackLink integration for Home Assistant."""

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from .const import DEFAULT_PORT, DEFAULT_SCAN_INTERVAL, DOMAIN
from .controller import RacklinkController
from .coordinator import RacklinkCoordinator

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
    scan_interval = entry.options.get(
        "scan_interval", DEFAULT_SCAN_INTERVAL.total_seconds()
    )

    _LOGGER.info(
        "Setting up Middle Atlantic Racklink integration for %s:%s (%s)",
        host,
        port,
        model,
    )

    # Create controller instance
    controller = RacklinkController(host, port, username, password)
    _LOGGER.debug("Controller instance created")

    # Start background connection with timeout
    try:
        # Attempt initial connection to device with timeout
        _LOGGER.debug("Starting background connection to %s", host)
        await controller.start_background_connection()

        # Set 20 second timeout for initial connection
        try:
            _LOGGER.debug("Waiting for initial connection (20 second timeout)")
            # Wait a bit for connection to establish
            await asyncio.sleep(5)

            if not controller.connected:
                _LOGGER.warning(
                    "Initial connection not established after 5 seconds, continuing with setup"
                )
            else:
                _LOGGER.info("Initial connection established successfully")

        except Exception as e:
            _LOGGER.warning("Error during initial connection to %s: %s", host, e)
            # Continue anyway - entities will show as unavailable until connected

    except Exception as e:
        _LOGGER.error("Error during initial setup: %s", e)
        # Continue anyway - Home Assistant will show entities as unavailable
        # until connection is established

    # Create the coordinator
    coordinator = RacklinkCoordinator(hass, controller, scan_interval)

    # Try to refresh the coordinator data
    try:
        await asyncio.wait_for(coordinator.async_refresh(), timeout=15)
        _LOGGER.info("Initial data refresh completed successfully")
    except asyncio.TimeoutError:
        _LOGGER.warning("Initial coordinator refresh timed out")
        raise ConfigEntryNotReady("Timed out getting initial data from device")
    except Exception as e:
        _LOGGER.warning("Error during initial coordinator refresh: %s", e)
        raise ConfigEntryNotReady(f"Error getting initial data: {e}")

    # Store the controller and coordinator
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "controller": controller,
        "coordinator": coordinator,
    }
    _LOGGER.debug("Controller and coordinator stored in hass.data")

    # Set up all platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Set up services
    async def async_cycle_all_outlets(call):
        """Cycle power for all outlets."""
        try:
            _LOGGER.info("Cycling all outlets on %s", controller.pdu_name)
            await asyncio.wait_for(controller.cycle_all_outlets(), timeout=15)
            # Force a refresh to update the states after cycling
            await coordinator.async_request_refresh()
        except asyncio.TimeoutError:
            _LOGGER.error("Cycling all outlets timed out")
        except Exception as e:
            _LOGGER.error("Error cycling all outlets: %s", e)

    hass.services.async_register(DOMAIN, "cycle_all_outlets", async_cycle_all_outlets)

    # Register cycle_outlet service
    async def async_cycle_outlet(call):
        """Cycle power for a specific outlet."""
        outlet = call.data.get("outlet")
        if outlet is None:
            _LOGGER.error("Outlet number is required for cycle_outlet service")
            return

        try:
            _LOGGER.info("Cycling outlet %s on %s", outlet, controller.pdu_name)
            await coordinator.cycle_outlet(int(outlet))
        except asyncio.TimeoutError:
            _LOGGER.error("Outlet cycle operation timed out")
        except Exception as e:
            _LOGGER.error("Error cycling outlet %s: %s", outlet, e)

    hass.services.async_register(DOMAIN, "cycle_outlet", async_cycle_outlet)

    # Register set_outlet_name service
    async def async_set_outlet_name(call):
        """Set the name of an outlet."""
        outlet = call.data.get("outlet")
        name = call.data.get("name")
        if outlet is not None and name is not None:
            try:
                await coordinator.set_outlet_name(int(outlet), name)
            except Exception as e:
                _LOGGER.error("Error setting outlet name: %s", e)
        else:
            _LOGGER.error("Both outlet number and name are required")

    hass.services.async_register(DOMAIN, "set_outlet_name", async_set_outlet_name)

    # Register set_pdu_name service
    async def async_set_pdu_name(call):
        """Set the name of the PDU."""
        name = call.data.get("name")
        if name is not None:
            try:
                await coordinator.set_pdu_name(name)
            except Exception as e:
                _LOGGER.error("Error setting PDU name: %s", e)
        else:
            _LOGGER.error("Name is required")

    hass.services.async_register(DOMAIN, "set_pdu_name", async_set_pdu_name)

    # Register function to shutdown controller on Home Assistant stop
    async def async_stop_controller(event):
        """Stop the controller when Home Assistant stops."""
        _LOGGER.debug("Stopping controller for %s", host)
        try:
            await controller.shutdown()
        except Exception as e:
            _LOGGER.error("Error shutting down controller: %s", e)

    # Listen for Home Assistant shutdown event
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, async_stop_controller)

    # Return success
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        # Get the controller from data dict
        data = hass.data[DOMAIN][entry.entry_id]
        controller = data["controller"]

        # Shut down the controller
        await controller.shutdown()

        # Remove entry from data dict
        hass.data[DOMAIN].pop(entry.entry_id)

        # If there are no more entries, remove the whole domain
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
