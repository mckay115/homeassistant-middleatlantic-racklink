"""mDNS discovery for Middle Atlantic RackLink devices."""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional, Set
from dataclasses import dataclass

from zeroconf import ServiceListener, ServiceBrowser, Zeroconf
from zeroconf.asyncio import AsyncServiceInfo
from homeassistant.components import zeroconf

_LOGGER = logging.getLogger(__name__)

# RackLink service types that we want to discover
RACKLINK_SERVICE_TYPES = [
    "_http._tcp.local.",
    "_https._tcp.local.",
    "_json-rpc._tcp.local.",
    "_telnet._tcp.local.",
    "_ssh._tcp.local.",
    "_snmp._udp.local.",
    "_modbus._tcp.local.",
]

# Keywords that might indicate a RackLink device
RACKLINK_IDENTIFIERS = [
    "racklink",
    "middle-atlantic",
    "middleatlantic",
    "pdu",
    "power-distribution",
    "legrand",
]


@dataclass
class DiscoveredDevice:
    """Represents a discovered RackLink device."""

    hostname: str
    ip_address: str
    port: int
    service_type: str
    name: str
    properties: Dict[str, str]

    @property
    def unique_id(self) -> str:
        """Return a unique identifier for this device."""
        return f"{self.hostname}_{self.ip_address}"

    @property
    def suggested_control_port(self) -> int:
        """Return the most likely control protocol port."""
        # If we found a JSON-RPC service, use that port
        if "json-rpc" in self.service_type:
            return self.port
        # If we found a telnet service, use that port
        if "_telnet" in self.service_type:
            return self.port
        # If we found an HTTPS service, likely Redfish
        if "_https" in self.service_type:
            return self.port
        # Otherwise, assume standard telnet control port
        return 6000


class RackLinkDiscovery:
    """Discovers RackLink devices using mDNS."""

    def __init__(self, hass) -> None:
        """Initialize the discovery service."""
        self._discovered_devices: Dict[str, DiscoveredDevice] = {}
        self._listeners: List[ServiceListener] = []
        self._browsers: List[ServiceBrowser] = []
        self._hass = hass
        self._zeroconf = None

    async def start_discovery(self, timeout: float = 10.0) -> List[DiscoveredDevice]:
        """Start mDNS discovery and return found devices.

        Args:
            timeout: How long to search for devices

        Returns:
            List of discovered RackLink devices
        """
        _LOGGER.info(
            "Starting mDNS discovery for RackLink devices (timeout: %ds)", timeout
        )

        try:
            # Use Home Assistant's shared Zeroconf instance
            self._zeroconf = await zeroconf.async_get_instance(self._hass)

            # Create a listener for each service type
            listener = RackLinkServiceListener(self._discovered_devices)

            # Browse for each service type
            for service_type in RACKLINK_SERVICE_TYPES:
                _LOGGER.debug("Browsing for service type: %s", service_type)
                browser = ServiceBrowser(self._zeroconf, service_type, listener)
                self._browsers.append(browser)

            # Wait for discovery to complete
            await asyncio.sleep(timeout)

            # Clean up browsers
            for browser in self._browsers:
                browser.cancel()
            self._browsers.clear()

            devices = list(self._discovered_devices.values())
            _LOGGER.info(
                "Discovery completed. Found %d potential RackLink devices", len(devices)
            )

            for device in devices:
                _LOGGER.info(
                    "Discovered: %s at %s:%d (%s)",
                    device.name,
                    device.ip_address,
                    device.port,
                    device.service_type,
                )

            return devices

        except Exception as err:
            _LOGGER.error("Error during mDNS discovery: %s", err)
            # Clean up browsers on error
            for browser in self._browsers:
                browser.cancel()
            self._browsers.clear()
            return []

    async def discover_single_device(self, hostname: str) -> Optional[DiscoveredDevice]:
        """Discover a specific device by hostname.

        Args:
            hostname: The hostname to look for (e.g., "racklink-1234.local")

        Returns:
            DiscoveredDevice if found, None otherwise
        """
        _LOGGER.info("Looking for specific device: %s", hostname)

        try:
            # Use Home Assistant's shared Zeroconf instance
            self._zeroconf = await zeroconf.async_get_instance(self._hass)

            # Try to resolve the hostname directly
            for service_type in RACKLINK_SERVICE_TYPES:
                service_name = f"{hostname}.{service_type}"
                _LOGGER.debug("Checking service: %s", service_name)

                info = await AsyncServiceInfo.async_request(
                    self._zeroconf, service_type, service_name, timeout=5000
                )

                if info and info.addresses:
                    # Convert to our format
                    ip_address = str(info.addresses[0])
                    properties = {
                        key.decode("utf-8"): value.decode("utf-8") if value else ""
                        for key, value in info.properties.items()
                    }

                    device = DiscoveredDevice(
                        hostname=hostname,
                        ip_address=ip_address,
                        port=info.port,
                        service_type=service_type,
                        name=info.name or hostname,
                        properties=properties,
                    )

                    _LOGGER.info(
                        "Found device: %s at %s:%d", hostname, ip_address, info.port
                    )
                    return device

            _LOGGER.warning("Device %s not found via mDNS", hostname)
            return None

        except Exception as err:
            _LOGGER.error("Error discovering device %s: %s", hostname, err)
            return None


class RackLinkServiceListener(ServiceListener):
    """Listener for RackLink mDNS services."""

    def __init__(self, discovered_devices: Dict[str, DiscoveredDevice]) -> None:
        """Initialize the listener."""
        self._discovered_devices = discovered_devices

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Called when a service is discovered."""
        _LOGGER.debug("Service discovered: %s (%s)", name, type_)

        # Check if this looks like a RackLink device
        if self._is_racklink_device(name):
            # Instead of trying to create async tasks from sync callback,
            # just store the service info and let the main discovery loop process it
            try:
                # Get basic service info synchronously
                info = zc.get_service_info(
                    type_, name, timeout=1000
                )  # 1 second timeout

                if info and info.addresses:
                    ip_address = str(info.addresses[0])
                    hostname = info.server.rstrip(".")

                    properties = {
                        key.decode("utf-8"): value.decode("utf-8") if value else ""
                        for key, value in (
                            info.properties.items() if info.properties else {}
                        ).items()
                    }

                    device = DiscoveredDevice(
                        hostname=hostname,
                        ip_address=ip_address,
                        port=info.port,
                        service_type=type_,
                        name=name,
                        properties=properties,
                    )

                    # Use hostname as the key to avoid duplicates from different services
                    self._discovered_devices[hostname] = device

                    _LOGGER.info(
                        "Discovered RackLink device: %s at %s:%d",
                        hostname,
                        ip_address,
                        info.port,
                    )
                else:
                    _LOGGER.debug("Service %s has no address info", name)
            except Exception as err:
                _LOGGER.debug("Could not process service %s: %s", name, err)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Called when a service is removed."""
        _LOGGER.debug("Service removed: %s (%s)", name, type_)

        # Remove from our discovered devices if present
        device_key = f"{name}_{type_}"
        if device_key in self._discovered_devices:
            del self._discovered_devices[device_key]

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Called when a service is updated."""
        _LOGGER.debug("Service updated: %s (%s)", name, type_)

        # Treat as a new discovery - same logic as add_service
        self.add_service(zc, type_, name)

    def _is_racklink_device(self, service_name: str) -> bool:
        """Check if a service name indicates a RackLink device."""
        name_lower = service_name.lower()

        # Check for RackLink identifiers in the name
        for identifier in RACKLINK_IDENTIFIERS:
            if identifier in name_lower:
                return True

        # Check for typical PDU naming patterns
        if any(pattern in name_lower for pattern in ["pdu", "power", "rack"]):
            return True

        return False


async def discover_racklink_devices(
    hass, timeout: float = 10.0
) -> List[DiscoveredDevice]:
    """Discover RackLink devices on the network.

    Args:
        hass: Home Assistant instance
        timeout: Discovery timeout in seconds

    Returns:
        List of discovered devices
    """
    discovery = RackLinkDiscovery(hass)
    return await discovery.start_discovery(timeout)


async def find_racklink_device(hass, hostname: str) -> Optional[DiscoveredDevice]:
    """Find a specific RackLink device by hostname.

    Args:
        hass: Home Assistant instance
        hostname: Device hostname to find

    Returns:
        DiscoveredDevice if found, None otherwise
    """
    discovery = RackLinkDiscovery(hass)
    return await discovery.discover_single_device(hostname)
