"""Binary sensor platform for Middle Atlantic Racklink."""

from __future__ import annotations

from .const import ATTR_MANUFACTURER, ATTR_MODEL, DOMAIN
from .controller.racklink_controller import RacklinkController
from .coordinator import RacklinkCoordinator
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

import logging

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Middle Atlantic Racklink binary sensors."""
    controller = hass.data[DOMAIN][config_entry.entry_id]

    # Get model capabilities to determine number of outlets
    capabilities = controller.get_model_capabilities()
    outlet_count = capabilities.get("num_outlets", 8)  # Default to 8 if not determined

    binary_sensors = []

    # Add surge protection sensor if model supports it
    if capabilities.get("has_surge_protection", False):
        binary_sensors.append(RacklinkSurgeProtection(controller))

    # Add non-critical binary sensor for each outlet
    for i in range(1, outlet_count + 1):
        binary_sensors.append(RacklinkOutletNonCritical(controller, i))

    if binary_sensors:
        async_add_entities(binary_sensors)


class RacklinkBinarySensor(BinarySensorEntity):
    """Base class for Racklink binary sensors."""

    def __init__(
        self,
        controller: RacklinkController,
        name: str,
        device_class: str | None,
        sensor_type: str,
    ) -> None:
        """Initialize the sensor."""
        self._controller = controller
        self._attr_name = name
        self._attr_device_class = device_class
        self._state = None
        self._sensor_type = sensor_type
        self._attr_unique_id = f"{controller.pdu_serial}_{self._sensor_type}"
        self._attr_available = False

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return self._attr_name

    @property
    def is_on(self) -> bool | None:
        """Return the state of the sensor."""
        return self._state

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._controller.connected and self._controller.available

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


class RacklinkSurgeProtection(RacklinkBinarySensor):
    """Surge protection binary sensor."""

    def __init__(self, controller: RacklinkController) -> None:
        """Initialize the surge protection sensor."""
        super().__init__(
            controller,
            "Racklink Surge Protection",
            BinarySensorDeviceClass.SAFETY,
            "surge_protection",
        )

    async def async_update(self) -> None:
        """Update the sensor state."""
        try:
            self._state = await self._controller.get_surge_protection_status()
            self._attr_available = (
                self._controller.connected and self._controller.available
            )
        except Exception as err:
            _LOGGER.error("Error updating surge protection sensor: %s", err)
            self._state = None
            self._attr_available = False


class RacklinkOutletNonCritical(RacklinkBinarySensor):
    """Outlet non-critical flag binary sensor."""

    def __init__(self, controller: RacklinkController, outlet: int) -> None:
        """Initialize the outlet non-critical sensor."""
        self._outlet = outlet

        # Get outlet name if available or create a default
        outlet_name = controller.outlet_names.get(outlet, f"Outlet {outlet}")

        # Always include outlet number in sensor name
        if outlet_name.startswith(f"Outlet {outlet}"):
            sensor_name = f"{outlet_name} Non-Critical"
        else:
            sensor_name = f"Outlet {outlet} - {outlet_name} Non-Critical"

        super().__init__(
            controller,
            sensor_name,
            BinarySensorDeviceClass.PROBLEM,
            f"outlet_{outlet}_non_critical",
        )
        self._outlet_name = outlet_name

    async def async_update(self) -> None:
        """Update the sensor state."""
        if not self._controller.connected:
            self._attr_available = False
            return

        try:
            # Update the name in case it changed
            new_outlet_name = self._controller.outlet_names.get(
                self._outlet, f"Outlet {self._outlet}"
            )
            if new_outlet_name != self._outlet_name:
                self._outlet_name = new_outlet_name
                # Always include outlet number in sensor name
                if self._outlet_name.startswith(f"Outlet {self._outlet}"):
                    self._attr_name = f"{self._outlet_name} Non-Critical"
                else:
                    self._attr_name = (
                        f"Outlet {self._outlet} - {self._outlet_name} Non-Critical"
                    )

            self._state = self._controller.outlet_non_critical.get(self._outlet, False)
            self._attr_available = (
                self._controller.connected
                and self._controller.available
                and self._controller.outlet_non_critical.get(self._outlet) is not None
            )
        except Exception as err:
            _LOGGER.error(
                "Error updating outlet %d non-critical sensor: %s", self._outlet, err
            )
            self._state = None
            self._attr_available = False
