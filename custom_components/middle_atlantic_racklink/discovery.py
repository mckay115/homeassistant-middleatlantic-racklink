"""mDNS discovery for Middle Atlantic RackLink devices."""

from __future__ import annotations

from dataclasses import dataclass
from homeassistant.components import zeroconf
from homeassistant.core import HomeAssistant
from typing import Dict, List, Optional, Set, Tuple
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
from zeroconf.asyncio import AsyncServiceInfo

import asyncio
import logging

_LOGGER = logging.getLogger(__name__)

# RackLink service types that we want to discover
RACKLINK_SERVICE_TYPES = [
    "_http._tcp.local.",
    "_https._tcp.local.",
    "_json-rpc._tcp.local.",
    "_telnet._tcp.local.",
]

# Keywords that identify a RackLink device
RACKLINK_IDENTIFIERS = [
    "racklink",
    "middle-atlantic",
    "middleatlantic",
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


class RackLinkServiceListener(ServiceListener):
    """Collects candidate RackLink mDNS services without blocking.

    Zeroconf invokes these callbacks from its own thread, so they must not
    perform network I/O; services are only recorded here and resolved
    asynchronously afterwards.
    """

    def __init__(self) -> None:
        """Initialize the listener."""
        self.pending_services: Set[Tuple[str, str]] = set()

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Record a discovered service for later async resolution."""
        if self._is_racklink_device(name):
            _LOGGER.debug("Candidate RackLink service: %s (%s)", name, type_)
            self.pending_services.add((type_, name))

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Handle a removed service."""
        self.pending_services.discard((type_, name))

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Handle an updated service."""
        self.add_service(zc, type_, name)

    @staticmethod
    def _is_racklink_device(service_name: str) -> bool:
        """Check if a service name indicates a RackLink device."""
        name_lower = service_name.lower()
        return any(identifier in name_lower for identifier in RACKLINK_IDENTIFIERS)


class RackLinkDiscovery:
    """Discovers RackLink devices using mDNS."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the discovery service."""
        self._hass = hass

    async def start_discovery(self, timeout: float = 10.0) -> List[DiscoveredDevice]:
        """Browse for RackLink devices and resolve their addresses.

        Args:
            timeout: How long to browse for devices

        Returns:
            List of discovered RackLink devices
        """
        _LOGGER.debug(
            "Starting mDNS discovery for RackLink devices (timeout: %.0fs)", timeout
        )

        browsers: List[ServiceBrowser] = []
        listener = RackLinkServiceListener()

        try:
            zc = await zeroconf.async_get_instance(self._hass)

            for service_type in RACKLINK_SERVICE_TYPES:
                browsers.append(ServiceBrowser(zc, service_type, listener))

            await asyncio.sleep(timeout)
        except Exception as err:
            _LOGGER.error("Error during mDNS discovery: %s", err)
            return []
        finally:
            for browser in browsers:
                browser.cancel()

        devices: Dict[str, DiscoveredDevice] = {}
        for type_, name in listener.pending_services:
            device = await self._resolve_service(zc, type_, name)
            if device:
                # Key by hostname to deduplicate multiple advertised services
                devices[device.hostname] = device

        result = list(devices.values())
        _LOGGER.debug("Discovery completed. Found %d RackLink devices", len(result))
        return result

    async def _resolve_service(
        self, zc: Zeroconf, type_: str, name: str
    ) -> Optional[DiscoveredDevice]:
        """Resolve a service's address info asynchronously."""
        try:
            info = AsyncServiceInfo(type_, name)
            if not await info.async_request(zc, timeout=3000):
                return None
            if not info.addresses:
                return None

            ip_address = info.parsed_addresses()[0]
            hostname = (info.server or name).rstrip(".")

            properties = {
                key.decode("utf-8"): value.decode("utf-8") if value else ""
                for key, value in (info.properties or {}).items()
            }

            _LOGGER.debug(
                "Discovered RackLink device: %s at %s:%s",
                hostname,
                ip_address,
                info.port,
            )
            return DiscoveredDevice(
                hostname=hostname,
                ip_address=ip_address,
                port=info.port or 0,
                service_type=type_,
                name=name,
                properties=properties,
            )
        except Exception as err:
            _LOGGER.debug("Could not resolve service %s: %s", name, err)
            return None


async def discover_racklink_devices(
    hass: HomeAssistant, timeout: float = 10.0
) -> List[DiscoveredDevice]:
    """Discover RackLink devices on the network."""
    return await RackLinkDiscovery(hass).start_discovery(timeout)
