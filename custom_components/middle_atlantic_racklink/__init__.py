"""Middle Atlantic RackLink integration for Home Assistant."""

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, DEFAULT_PORT
from .racklink_controller import RacklinkController

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

    # Create controller instance
    controller = RacklinkController(host, port, username, password, model)

    # Start background connection with extended timeout
    try:
        # Attempt initial connection to device
        _LOGGER.debug("Starting background connection to %s", host)
        await controller.start_background_connection()

        # Wait for background connection to establish
        await asyncio.sleep(2)

        # Force an initial update to populate data
        _LOGGER.debug("Triggering initial data refresh for %s", host)
        try:
            await asyncio.wait_for(controller.update(), timeout=10)
        except asyncio.TimeoutError:
            _LOGGER.warning("Initial update timed out, will retry in the background")
        except Exception as err:
            _LOGGER.warning("Error during initial update: %s", err)

    except Exception as e:
        _LOGGER.error("Error during initial setup: %s", e)
        # Continue anyway - Home Assistant will show entities as unavailable
        # until connection is established

    # Store the controller
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = controller

    # Set up all platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Create update coordinator to refresh the data
    async def async_update_data():
        """Fetch data from the controller."""
        try:
            # Check if controller is connected, if not, attempt reconnection
            if not controller.connected:
                _LOGGER.debug("Not connected during update, attempting reconnection")
                try:
                    await asyncio.wait_for(controller.connect(), timeout=5)
                except asyncio.TimeoutError:
                    _LOGGER.warning("Reconnection attempt timed out")
                    raise UpdateFailed("Connection timed out")
                except Exception as err:
                    _LOGGER.warning("Reconnection attempt failed: %s", err)
                    raise UpdateFailed(f"Reconnection failed: {err}")

            # Update all data from device
            if controller.connected:
                try:
                    # Use timeout for update to prevent blocking
                    await asyncio.wait_for(controller.update(), timeout=20)
                except asyncio.TimeoutError:
                    _LOGGER.warning("Controller update timed out")
                    raise UpdateFailed("Update timed out")
                except Exception as err:
                    _LOGGER.warning("Controller update failed: %s", err)
                    raise UpdateFailed(f"Update failed: {err}")

            # Signal entities to update
            async_dispatcher_send(hass, f"{DOMAIN}_entity_update")

            return True
        except Exception as e:
            _LOGGER.error("Error updating from device: %s", e)
            # Convert exception into update failure for coordinator
            raise UpdateFailed(f"Error communicating with device: {e}")

    # Create the update coordinator
    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"Racklink {host}",
        update_method=async_update_data,
        update_interval=DEFAULT_SCAN_INTERVAL,
    )

    # Start the initial refresh
    try:
        # Use a timeout to prevent blocking initial setup
        await asyncio.wait_for(coordinator.async_refresh(), timeout=15)
    except asyncio.TimeoutError:
        _LOGGER.warning(
            "Initial coordinator refresh timed out, will retry automatically"
        )
    except Exception as err:
        _LOGGER.warning("Error during initial coordinator refresh: %s", err)

    # Store coordinator in the data dict
    hass.data[DOMAIN][entry.entry_id + "_coordinator"] = coordinator

    # Set up services
    async def async_cycle_all_outlets(call):
        """Cycle power for all outlets."""
        await controller.cycle_all_outlets()

    hass.services.async_register(DOMAIN, "cycle_all_outlets", async_cycle_all_outlets)

    # Register set_outlet_name service
    async def async_set_outlet_name(call):
        """Set the name of an outlet."""
        outlet = call.data.get("outlet")
        name = call.data.get("name")
        if outlet is not None and name is not None:
            await controller.set_outlet_name(outlet, name)
            # Force refresh of coordinator after name change
            await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, "set_outlet_name", async_set_outlet_name)

    # Register set_pdu_name service
    async def async_set_pdu_name(call):
        """Set the name of the PDU."""
        name = call.data.get("name")
        if name is not None:
            await controller.set_pdu_name(name)
            # Force refresh of coordinator after name change
            await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, "set_pdu_name", async_set_pdu_name)

    # Register handler to cleanly close connection on shutdown
    async def async_stop_controller(event):
        """Stop the controller when Home Assistant stops."""
        _LOGGER.debug("Shutting down Racklink controller for %s", host)
        await controller.shutdown()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, async_stop_controller)
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug(
        "Unloading Middle Atlantic Racklink integration for %s", entry.data["host"]
    )

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        controller = hass.data[DOMAIN].pop(entry.entry_id)
        # Use the new shutdown method to properly clean up resources
        await controller.shutdown()
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
