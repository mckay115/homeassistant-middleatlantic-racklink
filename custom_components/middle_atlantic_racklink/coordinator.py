"""Data update coordinator for the Middle Atlantic RackLink integration."""

from __future__ import annotations

from .const import (
    ATTR_MANUFACTURER,
    ATTR_MODEL,
    CONF_SEQUENCE_DELAY,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SEQUENCE_DELAY,
    DOMAIN,
)
from .controller.racklink_controller import RacklinkController
from .exceptions import RacklinkAuthenticationError
from datetime import timedelta
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceInfo,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from typing import Any, cast, Dict

import logging

_LOGGER = logging.getLogger(__name__)


class RacklinkCoordinator(DataUpdateCoordinator[Dict[str, Any]]):
    """Coordinator to manage data updates from the RackLink controller."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        controller: RacklinkController,
        update_interval: timedelta = timedelta(seconds=DEFAULT_SCAN_INTERVAL),
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=update_interval,
        )
        self.controller = controller

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        device_info = DeviceInfo(
            identifiers={(DOMAIN, self.controller.pdu_serial or "unknown")},
            name=self.controller.pdu_name or "RackLink PDU",
            manufacturer=ATTR_MANUFACTURER,
            model=self.controller.pdu_model or ATTR_MODEL,
            sw_version=self.controller.pdu_firmware,
            configuration_url=f"https://{self.controller.host}",
        )
        if self.controller.mac_address:
            device_info["connections"] = {
                (CONNECTION_NETWORK_MAC, self.controller.mac_address)
            }
        return device_info

    @property
    def outlet_data(self) -> Dict[int, Dict[str, Any]]:
        """Return outlet data."""
        if self.data and "outlets" in self.data:
            return cast(Dict[int, Dict[str, Any]], self.data["outlets"])
        return {}

    @property
    def system_data(self) -> Dict[str, Any]:
        """Return system power data."""
        if self.data and "system" in self.data:
            return cast(Dict[str, Any], self.data["system"])
        return {}

    @property
    def status_data(self) -> Dict[str, Any]:
        """Return status information."""
        if self.data and "status" in self.data:
            return cast(Dict[str, Any], self.data["status"])
        return {}

    async def _async_update_data(self) -> Dict[str, Any]:
        """Update data from the PDU."""
        controller = self.controller

        try:
            if not controller.connected:
                _LOGGER.debug("Controller not connected, connecting")
                if not await controller.connect():
                    raise UpdateFailed("Failed to connect to PDU")

            if not await controller.update():
                # The connection might be stale; disconnect so the next
                # refresh performs a clean reconnect.
                await controller.disconnect()
                raise UpdateFailed("Failed to update PDU data")

        except RacklinkAuthenticationError as err:
            raise ConfigEntryAuthFailed(
                "Device rejected the configured credentials"
            ) from err
        except UpdateFailed:
            raise
        except Exception as err:
            await controller.disconnect()
            raise UpdateFailed(f"Error communicating with PDU: {err}") from err

        return self._build_data()

    def _build_data(self) -> Dict[str, Any]:
        """Build the coordinator data structure from controller state."""
        controller = self.controller

        outlets: Dict[int, Dict[str, Any]] = {}
        for outlet_num, state in controller.outlet_states.items():
            outlets[outlet_num] = {
                "state": state,
                "name": controller.outlet_names.get(outlet_num, f"Outlet {outlet_num}"),
                "attrs": controller.outlet_attrs.get(outlet_num),
                "power": controller.outlet_power_data.get(outlet_num),
                "energy_wh": controller.outlet_energy_data.get(outlet_num),
                "current": controller.outlet_current_data.get(outlet_num),
                "voltage": controller.outlet_voltage_data.get(outlet_num),
                "non_critical": controller.outlet_non_critical.get(outlet_num),
            }

        system = {
            "voltage": controller.rms_voltage,
            "current": controller.rms_current,
            "power": controller.active_power,
            "energy_wh": controller.active_energy,
            "frequency": controller.line_frequency,
            "apparent_power": controller.apparent_power,
            "power_factor": controller.power_factor,
        }

        status = {
            "load_shedding_active": controller.load_shedding_active,
            "sequence_active": controller.sequence_active,
            "surge_protection_ok": controller.surge_protection_ok,
        }

        return {"outlets": outlets, "system": system, "status": status}

    def _apply_optimistic_outlet_state(self, outlet: int, state: bool) -> None:
        """Publish an optimistic outlet state until the next poll confirms it."""
        if not self.data or outlet not in self.data.get("outlets", {}):
            return
        new_data = {
            **self.data,
            "outlets": {
                num: dict(outlet_data)
                for num, outlet_data in self.data["outlets"].items()
            },
        }
        new_data["outlets"][outlet]["state"] = state
        self.async_set_updated_data(new_data)

    def _apply_optimistic_status(self, key: str, value: bool) -> None:
        """Publish an optimistic status flag until the next poll confirms it."""
        if not self.data:
            return
        new_data = {**self.data, "status": {**self.data.get("status", {}), key: value}}
        self.async_set_updated_data(new_data)

    async def turn_outlet_on(self, outlet: int) -> None:
        """Turn an outlet on."""
        _LOGGER.debug("Turning outlet %d on", outlet)
        if not await self.controller.turn_outlet_on(outlet):
            raise HomeAssistantError(f"Failed to turn outlet {outlet} on")
        self._apply_optimistic_outlet_state(outlet, True)

    async def turn_outlet_off(self, outlet: int) -> None:
        """Turn an outlet off."""
        _LOGGER.debug("Turning outlet %d off", outlet)
        if not await self.controller.turn_outlet_off(outlet):
            raise HomeAssistantError(f"Failed to turn outlet {outlet} off")
        self._apply_optimistic_outlet_state(outlet, False)

    async def cycle_outlet(self, outlet: int) -> None:
        """Cycle an outlet."""
        _LOGGER.debug("Cycling outlet %d", outlet)
        if not await self.controller.cycle_outlet(outlet):
            raise HomeAssistantError(f"Failed to cycle outlet {outlet}")
        # The outlet ends up on after a completed cycle
        self._apply_optimistic_outlet_state(outlet, True)

    async def cycle_all_outlets(self) -> None:
        """Cycle all outlets."""
        _LOGGER.debug("Cycling all outlets")
        if not await self.controller.cycle_all_outlets():
            raise HomeAssistantError("Failed to cycle all outlets")
        if self.data:
            new_data = {
                **self.data,
                "outlets": {
                    num: {**outlet_data, "state": True}
                    for num, outlet_data in self.data.get("outlets", {}).items()
                },
            }
            self.async_set_updated_data(new_data)

    async def start_load_shedding(self) -> None:
        """Start load shedding."""
        _LOGGER.debug("Starting load shedding")
        if not await self.controller.start_load_shedding():
            raise HomeAssistantError("Failed to start load shedding")
        self._apply_optimistic_status("load_shedding_active", True)

    async def stop_load_shedding(self) -> None:
        """Stop load shedding."""
        _LOGGER.debug("Stopping load shedding")
        if not await self.controller.stop_load_shedding():
            raise HomeAssistantError("Failed to stop load shedding")
        self._apply_optimistic_status("load_shedding_active", False)

    @property
    def sequence_delay(self) -> int:
        """Return the configured delay between outlets when sequencing."""
        return int(
            self.config_entry.options.get(CONF_SEQUENCE_DELAY, DEFAULT_SEQUENCE_DELAY)
        )

    async def async_set_sequence_delay(self, delay: int) -> None:
        """Persist a new sequence delay in the config entry options."""
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            options={**self.config_entry.options, CONF_SEQUENCE_DELAY: delay},
        )

    async def start_sequence(self) -> None:
        """Start the outlet sequence."""
        _LOGGER.debug("Starting outlet sequence")
        if not await self.controller.start_sequence(self.sequence_delay):
            raise HomeAssistantError("Failed to start outlet sequence")
        self._apply_optimistic_status("sequence_active", True)

    async def stop_sequence(self) -> None:
        """Stop the outlet sequence."""
        _LOGGER.debug("Stopping outlet sequence")
        if not await self.controller.stop_sequence():
            raise HomeAssistantError("Failed to stop outlet sequence")
        self._apply_optimistic_status("sequence_active", False)

    async def set_outlet_name(self, outlet: int, name: str) -> None:
        """Set the user label of an outlet."""
        _LOGGER.debug("Setting outlet %d name to %s", outlet, name)
        if not await self.controller.set_outlet_name(outlet, name):
            raise HomeAssistantError(f"Failed to set name of outlet {outlet}")
        await self.async_request_refresh()

    async def set_pdu_name(self, name: str) -> None:
        """Set the PDU display name."""
        _LOGGER.debug("Setting PDU name to %s", name)
        if not await self.controller.set_pdu_name(name):
            raise HomeAssistantError("Failed to set PDU name")
        await self.async_request_refresh()
