"""Middle Atlantic RackLink integration for Home Assistant."""

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .racklink_controller import RacklinkController

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SWITCH,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Middle Atlantic Racklink from a config entry."""
    _LOGGER.debug(
        "Setting up Middle Atlantic Racklink integration for %s", entry.data["host"]
    )

    # Create controller but don't connect immediately - this prevents blocking boot
    controller = RacklinkController(
        entry.data["host"],
        entry.data["port"],
        entry.data["username"],
        entry.data["password"],
        entry.data.get("model", "AUTO_DETECT"),  # Get model or default to auto-detect
    )

    # Store controller in hass data
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = controller

    # Start non-blocking connection task
    await controller.start_background_connection()

    # Schedule platform setup as a background task to avoid blocking
    # This allows Home Assistant web UI to load faster
    async def setup_platforms():
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Launch platform setup in the background
    hass.async_create_task(setup_platforms())

    async def async_update(now=None) -> None:
        """Update the controller data."""
        # If not yet connected, try connecting
        if not controller.connected:
            try:
                _LOGGER.debug("Attempting to connect during update cycle")
                # Use timeout to prevent blocking
                await asyncio.wait_for(controller.connect(), timeout=10)
            except (asyncio.TimeoutError, Exception) as err:
                _LOGGER.error("Failed to connect during update: %s", err)
                return

        # Only fetch data if connected
        if controller.connected:
            try:
                # First, ensure we have the basic device info
                _LOGGER.debug("Updating device details")
                try:
                    await asyncio.wait_for(controller.get_pdu_details(), timeout=10)
                except Exception as err:
                    _LOGGER.error("Error getting PDU details: %s", err)

                # Then, get outlet details - try multiple times if needed
                _LOGGER.debug("Updating outlet states")
                outlet_retry = 2  # Try up to 3 times (initial + 2 retries)
                outlet_data_success = False

                while outlet_retry > 0 and not outlet_data_success:
                    try:
                        await asyncio.wait_for(
                            controller.get_all_outlet_states(force_refresh=True),
                            timeout=10,
                        )
                        # Check if we got data
                        if controller.outlet_states:
                            outlet_data_success = True
                            _LOGGER.debug(
                                "Successfully retrieved outlet states: %s",
                                controller.outlet_states,
                            )
                        else:
                            _LOGGER.warning(
                                "No outlet states data returned, retrying..."
                            )
                            outlet_retry -= 1
                            await asyncio.sleep(1)  # Short delay before retry
                    except Exception as err:
                        _LOGGER.error(
                            "Error getting outlet states (retry %d): %s",
                            2 - outlet_retry,
                            err,
                        )
                        outlet_retry -= 1
                        await asyncio.sleep(1)  # Short delay before retry

                # Get sensor values
                _LOGGER.debug("Updating sensor values")
                try:
                    await asyncio.wait_for(
                        controller.get_sensor_values(force_refresh=True), timeout=10
                    )
                    _LOGGER.debug("Retrieved sensor values: %s", controller.sensors)
                except Exception as err:
                    _LOGGER.error("Error getting sensor values: %s", err)

                # Update entity states
                _LOGGER.debug("Triggering entity update")
                async_dispatcher_send(hass, f"{DOMAIN}_entity_update")

            except asyncio.TimeoutError:
                _LOGGER.error("Update timed out for %s", entry.data["host"])
            except Exception as err:
                _LOGGER.error("Error updating Racklink data: %s", err)

    # Schedule periodic updates with a reasonable interval
    update_interval = DEFAULT_SCAN_INTERVAL
    entry.async_on_unload(
        async_track_time_interval(hass, async_update, update_interval)
    )

    # Initial update - does not block setup
    hass.async_create_task(async_update())

    async def cycle_all_outlets(call: ServiceCall) -> None:
        """Service to cycle all outlets."""
        _LOGGER.info("Cycling all outlets on %s", controller.pdu_name)
        try:
            # Add timeout to prevent blocking
            await asyncio.wait_for(controller.cycle_all_outlets(), timeout=10)
        except asyncio.TimeoutError:
            _LOGGER.error("Cycle all outlets timed out")
        except Exception as err:
            _LOGGER.error("Error cycling outlets: %s", err)

    hass.services.async_register(DOMAIN, "cycle_all_outlets", cycle_all_outlets)

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
