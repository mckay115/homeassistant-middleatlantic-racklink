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
    controller = RacklinkController(host, port, username, password)
    _LOGGER.debug("Controller instance created")

    # Start background connection with timeout
    try:
        # Attempt initial connection to device with timeout
        _LOGGER.debug("Starting background connection to %s", host)
        connection_task = controller.start_background_connection()
        # Set 20 second timeout for initial connection (increased from 15)
        try:
            _LOGGER.debug("Waiting for initial connection (20 second timeout)")
            await asyncio.wait_for(connection_task, timeout=20)
            _LOGGER.info("Initial connection established successfully")
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "Initial connection to %s timed out after 20 seconds, proceeding with setup anyway",
                host,
            )
            # Continue anyway - entities will show as unavailable until connected

        # Ensure update method is implemented and ready
        # This will allow entity updates to function correctly
        if not hasattr(controller, "update"):
            _LOGGER.warning(
                "Controller missing update method, patching with synchronization method"
            )

            async def update_wrapper():
                """Handle update requests."""
                _LOGGER.debug("Update request for %s", host)
                # Get outlet states
                await controller.get_all_outlet_states(force_refresh=True)
                # Get sensor values
                await controller.get_sensor_values(force_refresh=True)

            # Add update method to controller
            controller.update = update_wrapper

        # Wait longer for connection to establish - increase from 2 to 5 seconds
        _LOGGER.debug("Waiting 5 seconds for connection to stabilize")
        await asyncio.sleep(5)

        # Force an initial update to populate data with timeout
        _LOGGER.debug("Triggering initial data refresh for %s", host)
        try:
            _LOGGER.debug("Waiting for initial data refresh (15 second timeout)")
            await asyncio.wait_for(controller.update(), timeout=15)
            _LOGGER.info("Initial data refresh completed successfully")
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "Initial data refresh for %s timed out after 15 seconds", host
            )
            # Continue with setup - the coordinator will retry

    except Exception as e:
        _LOGGER.error("Error during initial setup: %s", e)
        # Continue anyway - Home Assistant will show entities as unavailable
        # until connection is established

    # Store the controller
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = controller
    _LOGGER.debug("Controller stored in hass.data")

    # Create update coordinator to refresh the data
    async def async_update_data():
        """Fetch data from the controller."""
        try:
            # Check if controller is connected, if not, attempt reconnection
            if not controller.connected:
                _LOGGER.debug("Controller not connected, attempting reconnection")
                try:
                    connection_result = await controller.reconnect()
                    _LOGGER.debug("Reconnection attempt result: %s", connection_result)

                    # If still not connected, raise exception but let coordinator handle it
                    if not controller.connected:
                        _LOGGER.warning("Connection not available, will retry later")
                        raise UpdateFailed("Device connection unavailable")
                except Exception as e:
                    _LOGGER.warning("Error during reconnection attempt: %s", e)
                    raise UpdateFailed(f"Connection error: {e}")

            # Check connection again before attempting update
            if controller.connected:
                _LOGGER.debug("Updating controller data from device")
                try:
                    # Use a timeout to prevent blocking indefinitely
                    success = await asyncio.wait_for(controller.update(), timeout=15)
                    _LOGGER.debug("Update result: %s", success)

                    if not success:
                        _LOGGER.warning(
                            "Controller update returned unsuccessful status"
                        )
                        # We'll still return data, but log the issue

                except asyncio.TimeoutError:
                    _LOGGER.warning("Data update timed out")
                    raise UpdateFailed("Data update timed out")
                except Exception as e:
                    _LOGGER.error("Error during controller update: %s", e)
                    raise UpdateFailed(f"Update error: {e}")

                # Get the latest device info
                device_info = await controller.get_device_info()
                _LOGGER.debug("Device info: %s", device_info)

                # Collect updated data for entities
                return {
                    "device_info": device_info,
                    "available": controller.available,
                    "outlet_states": controller.outlet_states.copy(),
                    "outlet_names": controller.outlet_names.copy(),
                    "outlet_power": controller.outlet_power.copy(),
                    "outlet_current": controller.outlet_current.copy(),
                    "outlet_energy": controller.outlet_energy.copy(),
                    "outlet_voltage": controller.outlet_voltage.copy(),
                    "outlet_power_factor": controller.outlet_power_factor.copy(),
                    "sensors": (
                        controller.sensors.copy()
                        if hasattr(controller, "sensors")
                        else {}
                    ),
                    "last_update": getattr(controller, "_last_update", 0),
                    "pdu_info": controller.pdu_info.copy(),
                }
            else:
                _LOGGER.warning("Controller is not connected, cannot update data")
                raise UpdateFailed("Device not connected")

        except UpdateFailed:
            # Re-raise UpdateFailed exceptions without modification
            raise
        except Exception as e:
            _LOGGER.error("Unexpected error updating from device: %s", e)
            # Convert exception into update failure for coordinator
            raise UpdateFailed(f"Error communicating with device: {e}")

    # Create the update coordinator with reasonable update interval
    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"Racklink {host}",
        update_method=async_update_data,
        update_interval=DEFAULT_SCAN_INTERVAL,
    )

    # Start the initial refresh with timeout
    try:
        await asyncio.wait_for(coordinator.async_refresh(), timeout=15)
    except asyncio.TimeoutError:
        _LOGGER.warning("Initial coordinator refresh timed out, proceeding with setup")
    except Exception as e:
        _LOGGER.warning("Error during initial coordinator refresh: %s", e)

    # Store coordinator in the data dict
    hass.data[DOMAIN][entry.entry_id + "_coordinator"] = coordinator

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
            await asyncio.wait_for(controller.cycle_outlet(outlet), timeout=10)
            # Force a refresh to update the states after cycling
            await coordinator.async_request_refresh()
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
                await asyncio.wait_for(
                    controller.set_outlet_name(outlet, name), timeout=5
                )
                # Force refresh of coordinator after name change
                await coordinator.async_request_refresh()
            except asyncio.TimeoutError:
                _LOGGER.error("Setting outlet name timed out")
            except Exception as e:
                _LOGGER.error("Error setting outlet %s name to %s: %s", outlet, name, e)

    hass.services.async_register(DOMAIN, "set_outlet_name", async_set_outlet_name)

    # Register set_pdu_name service
    async def async_set_pdu_name(call):
        """Set the name of the PDU."""
        name = call.data.get("name")
        if name is not None:
            try:
                await asyncio.wait_for(controller.set_pdu_name(name), timeout=5)
                # Force refresh of coordinator after name change
                await coordinator.async_request_refresh()
            except asyncio.TimeoutError:
                _LOGGER.error("Setting PDU name timed out")
            except Exception as e:
                _LOGGER.error("Error setting PDU name to %s: %s", name, e)

    hass.services.async_register(DOMAIN, "set_pdu_name", async_set_pdu_name)

    # Register handler to cleanly close connection on shutdown
    async def async_stop_controller(event):
        """Stop the controller when Home Assistant stops."""
        _LOGGER.debug("Shutting down Racklink controller for %s", host)
        try:
            await asyncio.wait_for(controller.shutdown(), timeout=5)
        except asyncio.TimeoutError:
            _LOGGER.warning("Controller shutdown timed out")
        except Exception as e:
            _LOGGER.warning("Error during controller shutdown: %s", e)

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
        try:
            await asyncio.wait_for(controller.shutdown(), timeout=5)
            _LOGGER.info(
                "Disconnected from Middle Atlantic Racklink at %s", entry.data["host"]
            )
        except asyncio.TimeoutError:
            _LOGGER.warning("Controller shutdown timed out during unload")
        except Exception as e:
            _LOGGER.warning("Error during controller shutdown: %s", e)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    _LOGGER.debug(
        "Reloading Middle Atlantic Racklink integration for %s", entry.data["host"]
    )
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
