"""Switch platform for Middle Atlantic Racklink."""

import asyncio
import logging
import re
from typing import Any, Optional

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import ATTR_MANUFACTURER, ATTR_MODEL, DOMAIN
from .racklink_controller import RacklinkController

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Middle Atlantic Racklink switches from config entry."""
    controller = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = hass.data[DOMAIN][config_entry.entry_id + "_coordinator"]
    switches = []

    # Get model capabilities to determine number of outlets
    capabilities = controller.get_model_capabilities()
    outlet_count = capabilities.get("num_outlets", 8)  # Default to 8 if not determined

    _LOGGER.info(
        "Setting up %d outlet switches for %s (%s)",
        outlet_count,
        controller.pdu_name,
        controller.pdu_model,
    )

    # Add switches even if device is not yet available
    # They will show as unavailable until connection is established
    for outlet in range(1, outlet_count + 1):
        switches.append(RacklinkOutlet(coordinator, controller, config_entry, outlet))

    async_add_entities(switches)

    async def cycle_outlet(call: ServiceCall) -> None:
        """Cycle power for a specific outlet."""
        outlet = call.data.get("outlet")
        if not outlet:
            _LOGGER.error("Outlet number is required for cycle_outlet service")
            return

        _LOGGER.info("Cycling outlet %s on %s", outlet, controller.pdu_name)
        try:
            await asyncio.wait_for(controller.cycle_outlet(outlet), timeout=10)
        except asyncio.TimeoutError:
            _LOGGER.error("Outlet cycle operation timed out")
        except Exception as err:
            _LOGGER.error("Error cycling outlet %s: %s", outlet, err)

    hass.services.async_register(DOMAIN, "cycle_outlet", cycle_outlet)


class RacklinkOutlet(CoordinatorEntity, SwitchEntity):
    """Representation of a Racklink outlet switch."""

    def __init__(self, coordinator, controller, config_entry, outlet):
        """Initialize the outlet switch."""
        super().__init__(coordinator)
        self._controller = controller
        self._config_entry = config_entry
        self._outlet = outlet
        self._pending_update = False
        self._coordinator = coordinator

        # Get outlet name if available or create a default
        if self._controller.outlet_names and outlet in self._controller.outlet_names:
            self._outlet_name = self._controller.outlet_names[outlet]
        else:
            self._outlet_name = f"Outlet {outlet}"

        # Get PDU info safely with fallbacks
        self._pdu_model = getattr(self._controller, "pdu_model", "RackLink PDU")
        self._pdu_name = getattr(self._controller, "pdu_name", "RackLink")
        self._pdu_serial = getattr(self._controller, "pdu_serial", f"Unknown_{outlet}")
        self._pdu_firmware = getattr(self._controller, "pdu_firmware", "Unknown")

        # Set entity attributes - always include outlet number in name
        if self._outlet_name.startswith(f"Outlet {outlet}"):
            self._attr_name = self._outlet_name
        else:
            self._attr_name = f"Outlet {outlet} - {self._outlet_name}"
        self._attr_unique_id = f"{self._pdu_serial}_outlet_{outlet}"

        _LOGGER.debug(
            "Initialized outlet switch %s on PDU %s (outlet %s)",
            self._outlet_name,
            self._pdu_name,
            outlet,
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        device_info = {
            "identifiers": {(DOMAIN, self._controller.pdu_serial)},
            "name": f"Racklink PDU {self._controller.pdu_name}",
            "manufacturer": ATTR_MANUFACTURER,
            "model": self._controller.pdu_model or ATTR_MODEL,
            "sw_version": self._controller.pdu_firmware,
        }

        # Add MAC address as a connection info if available
        if self._controller.mac_address:
            device_info["connections"] = {("mac", self._controller.mac_address)}

        return device_info

    @property
    def is_on(self) -> Optional[bool]:
        """Return true if outlet is on."""
        if self.coordinator.data:
            # Get state from coordinator data
            outlet_states = self.coordinator.data.get("outlet_states", {})
            return outlet_states.get(self._outlet, None)
        # Fall back to controller state if coordinator data is not available
        return self._controller.outlet_states.get(self._outlet, None)

    @property
    def available(self) -> bool:
        """Return if switch is available."""
        # Use controller connection status to determine availability
        coordinator_has_data = (
            self.coordinator.data
            and self._outlet in self.coordinator.data.get("outlet_states", {})
        )
        controller_has_data = self._outlet in self._controller.outlet_states

        return (
            self._controller.connected
            and self._controller.available
            and (coordinator_has_data or controller_has_data)
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the outlet on."""
        _LOGGER.debug("Turning outlet %s ON", self._outlet)

        try:
            # Use the controller's built-in method which includes verification
            success = await self._controller.turn_outlet_on(self._outlet)

            if success:
                _LOGGER.debug("Successfully turned outlet %s ON", self._outlet)
                # Update internal state
                self._controller.outlet_states[self._outlet] = True
                # Trigger state update
                self.async_write_ha_state()

                # Request a refresh through the coordinator to update all entities
                async_call_later(self.hass, 2, self._async_refresh_state)
            else:
                _LOGGER.warning("Failed to turn outlet %s ON", self._outlet)
        except Exception as e:
            _LOGGER.error("Error turning outlet %s ON: %s", self._outlet, e)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the outlet off."""
        _LOGGER.debug("Turning outlet %s OFF", self._outlet)

        try:
            # Use the controller's built-in method which includes verification
            success = await self._controller.turn_outlet_off(self._outlet)

            if success:
                _LOGGER.debug("Successfully turned outlet %s OFF", self._outlet)
                # Update internal state
                self._controller.outlet_states[self._outlet] = False
                # Trigger state update
                self.async_write_ha_state()

                # Request a refresh through the coordinator to update all entities
                async_call_later(self.hass, 2, self._async_refresh_state)
            else:
                _LOGGER.warning("Failed to turn outlet %s OFF", self._outlet)
        except Exception as e:
            _LOGGER.error("Error turning outlet %s OFF: %s", self._outlet, e)

    async def _async_refresh_state(self, _now=None):
        """Refresh all states after a control operation."""
        _LOGGER.debug("Requesting data refresh for outlet %s", self._outlet)
        try:
            # Force refresh of outlet states through the coordinator
            await self.coordinator.async_request_refresh()
        except Exception as e:
            _LOGGER.error("Error refreshing states: %s", e)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional outlet information."""
        attrs = {
            "outlet_number": self._outlet,
            "can_cycle": True,
        }

        # Try to get data from coordinator first
        if self.coordinator.data:
            # Add all available power and metrics data from coordinator
            if (
                "outlet_power" in self.coordinator.data
                and self._outlet in self.coordinator.data["outlet_power"]
            ):
                power = self.coordinator.data["outlet_power"].get(self._outlet)
                if power is not None:
                    attrs["power"] = f"{power:.1f} W"

            if (
                "outlet_current" in self.coordinator.data
                and self._outlet in self.coordinator.data["outlet_current"]
            ):
                current = self.coordinator.data["outlet_current"].get(self._outlet)
                if current is not None:
                    attrs["current"] = f"{current:.2f} A"

            if (
                "outlet_energy" in self.coordinator.data
                and self._outlet in self.coordinator.data["outlet_energy"]
            ):
                energy = self.coordinator.data["outlet_energy"].get(self._outlet)
                if energy is not None:
                    attrs["energy"] = f"{energy:.1f} Wh"

            if (
                "outlet_power_factor" in self.coordinator.data
                and self._outlet in self.coordinator.data["outlet_power_factor"]
            ):
                power_factor = self.coordinator.data["outlet_power_factor"].get(
                    self._outlet
                )
                if power_factor is not None:
                    attrs["power_factor"] = f"{power_factor:.2f}"

            if (
                "outlet_voltage" in self.coordinator.data
                and self._outlet in self.coordinator.data["outlet_voltage"]
            ):
                voltage = self.coordinator.data["outlet_voltage"].get(self._outlet)
                if voltage is not None:
                    attrs["voltage"] = f"{voltage:.1f} V"

            if (
                "outlet_apparent_power" in self.coordinator.data
                and self._outlet in self.coordinator.data["outlet_apparent_power"]
            ):
                apparent_power = self.coordinator.data["outlet_apparent_power"].get(
                    self._outlet
                )
                if apparent_power is not None:
                    attrs["apparent_power"] = f"{apparent_power:.1f} VA"

            if (
                "outlet_line_frequency" in self.coordinator.data
                and self._outlet in self.coordinator.data["outlet_line_frequency"]
            ):
                frequency = self.coordinator.data["outlet_line_frequency"].get(
                    self._outlet
                )
                if frequency is not None:
                    attrs["frequency"] = f"{frequency:.1f} Hz"
        else:
            # Fall back to controller data if coordinator data is not available
            if power := self._controller.outlet_power.get(self._outlet):
                attrs["power"] = f"{power:.1f} W"
            if current := self._controller.outlet_current.get(self._outlet):
                attrs["current"] = f"{current:.2f} A"
            if energy := self._controller.outlet_energy.get(self._outlet):
                attrs["energy"] = f"{energy:.1f} Wh"
            if power_factor := self._controller.outlet_power_factor.get(self._outlet):
                attrs["power_factor"] = f"{power_factor:.2f}"
            if voltage := self._controller.outlet_voltage.get(self._outlet):
                attrs["voltage"] = f"{voltage:.1f} V"
            if apparent_power := self._controller.outlet_apparent_power.get(
                self._outlet
            ):
                attrs["apparent_power"] = f"{apparent_power:.1f} VA"
            if frequency := self._controller.outlet_line_frequency.get(self._outlet):
                attrs["frequency"] = f"{frequency:.1f} Hz"

        return attrs

    async def async_cycle(self) -> None:
        """Cycle the outlet power."""
        try:
            _LOGGER.debug("Cycling outlet %d", self._outlet)
            await asyncio.wait_for(
                self._controller.cycle_outlet(self._outlet), timeout=10
            )
            # Refresh coordinator data after cycling
            await self.coordinator.async_request_refresh()
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout cycling outlet %s", self._outlet)
        except Exception as err:
            _LOGGER.error("Error cycling outlet %s: %s", self._outlet, err)
