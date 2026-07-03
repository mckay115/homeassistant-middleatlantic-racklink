"""Controller for Middle Atlantic RackLink PDUs."""

from __future__ import annotations

from typing import Any, Dict, Optional, Union

import asyncio
import logging
import re

from ..connection_factory import AutoConnectionManager, ConnectionFactory
from ..const import (
    CONF_CONNECTION_TYPE,
    CONNECTION_TYPE_AUTO,
    CONNECTION_TYPE_REDFISH,
    CONNECTION_TYPE_TELNET,
    DEFAULT_PORT,
)
from ..exceptions import RacklinkAuthenticationError
from ..redfish_connection import RedfishConnection
from ..socket_connection import (
    OUTLET_CYCLE,
    OUTLET_OFF,
    OUTLET_ON,
    SocketConfig,
    SocketConnection,
)

_LOGGER = logging.getLogger(__name__)


class RacklinkController:
    """Controller class for Middle Atlantic RackLink PDUs."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        timeout: int = 20,
        connection_type: str = CONNECTION_TYPE_AUTO,
        use_https: bool = True,
        enable_vendor_features: bool = True,
    ) -> None:
        """Initialize the controller.

        Args:
            host: Hostname or IP address of the device
            port: Port number for the connection
            username: Username for authentication
            password: Password for authentication
            timeout: Timeout for socket operations in seconds
            connection_type: Type of connection (redfish, telnet, auto)
            use_https: Whether to use HTTPS for Redfish connections
            enable_vendor_features: Whether to open a secondary telnet
                connection for vendor features when using Redfish
        """
        # Validate host early and normalize for ID usage
        if not host or not isinstance(host, str) or not host.strip():
            _LOGGER.warning(
                "Host was empty or None during controller init; defaulting to 'unknown'"
            )
            host = "unknown"

        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout
        self.connection_type = connection_type
        self.use_https = use_https
        self.enable_vendor_features = enable_vendor_features

        # Store configuration for connection creation
        self._config_data = {
            "host": host,
            "port": port,
            "username": username,
            "password": password,
            "timeout": timeout,
            CONF_CONNECTION_TYPE: connection_type,
            "use_https": use_https,
        }

        # Connection will be created during connect()
        self.connection: Optional[Union[RedfishConnection, SocketConnection]] = None

        # Primary socket connection (telnet/binary mode)
        self.socket: Optional[SocketConnection] = None

        # Hybrid mode: secondary telnet connection for vendor-specific features
        self._telnet_connection: Optional[SocketConnection] = None
        self._hybrid_mode = False

        # Helper for safe host formatting in IDs
        self._host_safe_id = self._safe_host_for_id(host)

        # Device information
        self.pdu_name: str = f"RackLink PDU {host}"
        self.pdu_model: str = "RackLink"
        self.pdu_firmware: str = "Unknown"
        self.pdu_serial: str = f"PDU-{self._host_safe_id}"  # Default fallback
        self.mac_address: str = ""

        # System power data (energy is stored in Wh internally)
        self.rms_voltage: Optional[float] = None
        self.rms_current: Optional[float] = None
        self.active_power: Optional[float] = None
        self.active_energy: Optional[float] = None
        self.line_frequency: Optional[float] = None
        self.apparent_power: Optional[float] = None
        self.power_factor: Optional[float] = None

        # Outlet data
        self.outlet_states: Dict[int, bool] = {}
        self.outlet_names: Dict[int, str] = {}
        self.outlet_power_data: Dict[int, float] = {}
        self.outlet_energy_data: Dict[int, float] = {}  # Wh
        self.outlet_current_data: Dict[int, float] = {}
        self.outlet_voltage_data: Dict[int, float] = {}
        self.outlet_non_critical: Dict[int, bool] = {}
        self.outlet_attrs: Dict[int, Dict[str, Any]] = {}

        # Status flags
        self.connected: bool = False
        self.available: bool = False
        self.per_outlet_metrics_available: bool = False
        self.load_shedding_active: Optional[bool] = None
        self.sequence_active: Optional[bool] = None
        self.surge_protection_ok: Optional[bool] = None

        self._last_details_fetch: float = 0.0
        self._details_refresh_interval_s: int = 600
        self._last_telnet_metrics_fetch: float = 0.0
        self._telnet_metrics_interval_s: int = 30

    @property
    def has_vendor_features(self) -> bool:
        """Return True if a telnet channel for vendor features is available."""
        return bool(self._get_telnet_connection())

    def _get_telnet_connection(self) -> Optional[SocketConnection]:
        """Return the telnet-capable connection (primary or hybrid)."""
        return self.socket or self._telnet_connection

    async def connect(self) -> bool:
        """Connect to the PDU.

        Raises:
            RacklinkAuthenticationError: If the device rejects the credentials.
        """
        try:
            _LOGGER.info(
                "Connecting to RackLink PDU at %s:%s (type: %s)",
                self.host,
                self.port,
                self.connection_type,
            )

            # Close any previous connections before creating new ones so we
            # never leak sockets or aiohttp sessions across reconnects.
            await self._close_connections()

            # Create connection based on type
            if self.connection_type == CONNECTION_TYPE_AUTO:
                auto_config = self._config_data.copy()
                # Remove port to let auto-detection try all standard ports
                auto_config.pop("port", None)

                self.connection = await AutoConnectionManager.detect_best_connection(
                    auto_config
                )
                # Update connection type and port based on what was detected
                if isinstance(self.connection, RedfishConnection):
                    self.connection_type = CONNECTION_TYPE_REDFISH
                    self.port = self.connection.config.port
                    _LOGGER.info(
                        "Auto-detected Redfish connection on port %d", self.port
                    )
                else:
                    self.connection_type = CONNECTION_TYPE_TELNET
                    self.port = self.connection.config.port
                    _LOGGER.info(
                        "Auto-detected Telnet/Binary connection on port %d",
                        self.port,
                    )
            else:
                # Create specific connection type
                self.connection = ConnectionFactory.create_connection(self._config_data)
                if not await self.connection.connect():
                    _LOGGER.error("Failed to connect to RackLink PDU")
                    self.connected = False
                    self.available = False
                    return False

            # Keep a reference to the socket connection for telnet features
            if isinstance(self.connection, SocketConnection):
                self.socket = self.connection
            elif self.enable_vendor_features:
                # For Redfish connections, create secondary telnet connection
                await self._setup_hybrid_telnet_connection()

            self.connected = True
            self.available = True

            if isinstance(self.connection, RedfishConnection):
                if self._hybrid_mode:
                    _LOGGER.info(
                        "Connected to RackLink PDU (Redfish + Telnet hybrid mode)"
                    )
                else:
                    _LOGGER.info("Connected to RackLink PDU (Redfish mode)")
            else:
                _LOGGER.info("Connected to RackLink PDU (Telnet/Binary mode)")
            return True

        except RacklinkAuthenticationError:
            self.connected = False
            self.available = False
            raise
        except Exception as err:
            _LOGGER.error("Error connecting to RackLink PDU: %s", err)
            self.connected = False
            self.available = False
            return False

    def _safe_host_for_id(self, value: Optional[str]) -> str:
        """Return a filesystem/ID safe string from host/IP."""
        base = (value or "unknown").strip()
        return base.replace(".", "-").replace(":", "-")

    async def _close_connections(self) -> None:
        """Close the current primary and hybrid connections if present."""
        if self.connection:
            try:
                await self.connection.disconnect()
            except Exception as err:
                _LOGGER.debug("Error closing previous connection: %s", err)
            self.connection = None
            self.socket = None

        if self._telnet_connection:
            try:
                await self._telnet_connection.disconnect()
            except Exception as err:
                _LOGGER.debug("Error closing previous telnet connection: %s", err)
            self._telnet_connection = None
            self._hybrid_mode = False

    async def disconnect(self) -> None:
        """Disconnect from the PDU."""
        await self._close_connections()
        self.connected = False
        self.available = False
        _LOGGER.info("Disconnected from RackLink PDU")

    async def _setup_hybrid_telnet_connection(self) -> None:
        """Set up secondary telnet connection for vendor-specific features."""
        try:
            _LOGGER.debug("Setting up hybrid telnet connection for vendor features")

            socket_config = SocketConfig(
                host=self.host,
                port=DEFAULT_PORT,
                username=self.username,
                password=self.password,
                timeout=self.timeout,
            )

            telnet_connection = SocketConnection(socket_config)

            if await telnet_connection.connect():
                self._telnet_connection = telnet_connection
                self._hybrid_mode = True
                _LOGGER.info(
                    "Hybrid telnet connection established for vendor features "
                    "(load shedding, sequencing)"
                )
            else:
                _LOGGER.warning(
                    "Could not establish hybrid telnet connection - vendor "
                    "features unavailable"
                )

        except RacklinkAuthenticationError:
            _LOGGER.warning(
                "Hybrid telnet authentication failed - vendor features unavailable"
            )
        except Exception as err:
            _LOGGER.warning(
                "Hybrid telnet setup failed: %s - vendor features unavailable", err
            )

    async def update(self) -> bool:
        """Update PDU data.

        Raises:
            RacklinkAuthenticationError: If the device rejects the credentials.
        """
        try:
            if not self.connected:
                _LOGGER.info("Not connected, attempting to connect before update")
                if not await self.connect():
                    _LOGGER.error("Connection failed during update")
                    return False

            # Check if Redfish authentication has expired and reconnect if needed
            if (
                isinstance(self.connection, RedfishConnection)
                and not self.connection.authenticated
            ):
                _LOGGER.warning(
                    "Redfish authentication expired, attempting to reconnect"
                )
                if not await self.connect():
                    _LOGGER.error("Reconnection failed during update")
                    return False

            _LOGGER.debug("Updating PDU details")
            await self._update_pdu_details()

            _LOGGER.debug("Updating outlet states")
            await self._update_outlet_states()

            _LOGGER.debug("Updating system status")
            await self._update_system_status()

            if isinstance(self.connection, RedfishConnection):
                _LOGGER.debug("Updating per-outlet metrics via Redfish")
                await self._update_redfish_outlet_data()
                await self._update_redfish_pdu_metrics()

            self.available = True
            _LOGGER.debug("Update completed successfully")
            return True
        except RacklinkAuthenticationError:
            self.available = False
            raise
        except Exception as err:
            _LOGGER.error("Error updating PDU data: %s", err)
            self.available = False
            return False

    async def _update_redfish_pdu_metrics(self) -> None:
        """Fetch PDU-level metrics via the Redfish Metrics endpoint."""
        try:
            metrics = await self.connection.get_power_metrics()
        except RacklinkAuthenticationError:
            raise
        except Exception as metrics_err:
            _LOGGER.debug("Redfish metrics fetch failed: %s", metrics_err)
            return

        if not metrics:
            return

        if metrics.get("power") is not None:
            self.active_power = float(metrics["power"])
        if metrics.get("energy") is not None:
            # The Redfish EnergykWh field reports kWh; store Wh internally
            self.active_energy = float(metrics["energy"]) * 1000.0
        if metrics.get("power_factor") is not None:
            self.power_factor = float(metrics["power_factor"])
        if metrics.get("apparent_power") is not None:
            self.apparent_power = float(metrics["apparent_power"])

    async def _update_pdu_details(self) -> None:
        """Update PDU details."""
        # Throttle PDU details to reduce overhead
        now = asyncio.get_event_loop().time()
        if (
            self._last_details_fetch
            and (now - self._last_details_fetch) < self._details_refresh_interval_s
        ):
            _LOGGER.debug("Skipping PDU details refresh (throttled)")
            return
        _LOGGER.debug("Fetching PDU details")

        if isinstance(self.connection, RedfishConnection):
            pdu_info = await self.connection.get_pdu_info()

            if pdu_info:
                self.pdu_name = pdu_info.get("Name", f"RackLink PDU {self.host}")
                self.pdu_model = pdu_info.get("Model", "RackLink")
                self.pdu_firmware = pdu_info.get("FirmwareVersion", "Unknown")
                self.pdu_serial = pdu_info.get(
                    "SerialNumber", f"PDU-{self._host_safe_id}"
                )
                self._parse_redfish_health(pdu_info)
                _LOGGER.debug("Retrieved PDU info via Redfish")
            else:
                _LOGGER.warning("Could not get PDU info via Redfish")

            self._last_details_fetch = now
            return

        # Telnet/binary path
        response = ""
        for cmd in ("show pdu details", "show pdu", "show system"):
            response = await self.connection.send_command(cmd)
            if response and "Unknown command" not in response:
                _LOGGER.debug("Got PDU data with command: %s", cmd)
                break
            await asyncio.sleep(1.0)

        # Add delay between commands to prevent session corruption
        await asyncio.sleep(0.5)

        # Parse PDU name - format: PDU 'LiskoLabs Rack'
        name_match = re.search(r"PDU ['\"](.*?)['\"]", response)
        if name_match:
            self.pdu_name = name_match.group(1)

        # Parse PDU model - format: Model:            RLNK-P920R
        model_match = re.search(r"Model:\s*(.+?)(?:\r|\n)", response)
        if model_match:
            self.pdu_model = model_match.group(1).strip()

        # Parse firmware version - format: Firmware Version: 2.2.0.1-51126
        fw_match = re.search(r"Firmware Version:\s*(.+?)(?:\r|\n)", response)
        if fw_match:
            self.pdu_firmware = fw_match.group(1).strip()

        # Parse serial number - format: Serial Number:    RLNKP-920_050a82
        sn_match = re.search(r"Serial Number:\s*(.+?)(?:\r|\n)", response)
        if sn_match:
            self.pdu_serial = sn_match.group(1).strip()

        # Get MAC address
        _LOGGER.debug("Fetching network interface details")
        await asyncio.sleep(0.5)

        network_response = ""
        for cmd in (
            "show network interface eth1",
            "show network interface eth0",
            "show network",
            "show interface",
        ):
            network_response = await self.connection.send_command(cmd)
            if network_response and "Unknown command" not in network_response:
                _LOGGER.debug("Got network data with command: %s", cmd)
                break
            await asyncio.sleep(1.0)

        await asyncio.sleep(0.5)

        # Parse MAC address - format: MAC address: 00:1e:c5:05:0a:82
        mac_match = re.search(r"MAC address:\s*(.+?)(?:\r|\n|,)", network_response)
        if mac_match:
            self.mac_address = mac_match.group(1).strip()

            # If serial number is still the host-based fallback, prefer MAC
            if self.pdu_serial.startswith("PDU-"):
                self.pdu_serial = f"MAC-{self.mac_address.replace(':', '')}"
        else:
            _LOGGER.debug("Could not parse MAC address from response")

        self._last_details_fetch = now

    def _parse_redfish_health(self, pdu_info: Dict[str, Any]) -> None:
        """Derive surge protection / health status from Redfish PDU info."""
        status = pdu_info.get("Status")
        if isinstance(status, dict):
            health = status.get("Health")
            if isinstance(health, str):
                self.surge_protection_ok = health.lower() in ("ok", "healthy", "normal")
                return
        protected = pdu_info.get("SurgeProtected") or pdu_info.get("Protected")
        if isinstance(protected, bool):
            self.surge_protection_ok = protected

    async def _update_outlet_states(self) -> None:
        """Update outlet states using appropriate protocol."""
        if isinstance(self.connection, RedfishConnection):
            _LOGGER.debug("Fetching outlet states using Redfish API")
            info_map = await self.connection.get_all_outlets_info()

            if info_map:
                for outlet_num, data in info_map.items():
                    state = data.get("state")
                    if state is not None:
                        self.outlet_states[outlet_num] = bool(state)
                    if data.get("name"):
                        self.outlet_names[outlet_num] = data["name"]
                    if data.get("attrs"):
                        self.outlet_attrs[outlet_num] = data["attrs"]
            else:
                _LOGGER.warning("No outlet data found via Redfish")
            return

        if not isinstance(self.connection, SocketConnection):
            return

        if self.connection.protocol_type == "telnet":
            _LOGGER.debug("Fetching outlet states using Telnet protocol")
            await asyncio.sleep(0.5)  # Prevent rapid commands
            outlet_data = await self.connection.telnet_read_outlet_states()

            if outlet_data:
                for outlet_num, state in outlet_data.items():
                    self.outlet_states[outlet_num] = state

                if self.connection.outlet_names:
                    self.outlet_names.update(self.connection.outlet_names)
                else:
                    for outlet_num in outlet_data:
                        self.outlet_names.setdefault(
                            outlet_num, f"Outlet {outlet_num}"
                        )
            else:
                _LOGGER.warning("No outlet states found via Telnet")
            return

        # Binary protocol
        _LOGGER.debug("Fetching outlet states using binary protocol")

        if not self.outlet_states:
            # Try outlets 1-16 to discover available outlets
            outlet_range = range(1, 17)
            discovering = True
        else:
            outlet_range = list(self.outlet_states.keys())
            discovering = False

        outlet_data = {}
        for outlet_num in outlet_range:
            state = await self.connection.read_outlet_state(outlet_num)
            if state is not None:
                outlet_data[outlet_num] = state
            elif discovering:
                # No response while discovering means we've found the limit
                break

        if outlet_data:
            for outlet_num, state in outlet_data.items():
                self.outlet_states[outlet_num] = state
                self.outlet_names.setdefault(outlet_num, f"Outlet {outlet_num}")
        else:
            _LOGGER.warning("No outlet states found via binary protocol")

    async def _update_system_status(self) -> None:
        """Update system status using actual device power measurements."""
        try:
            telnet_conn = self._get_telnet_connection()

            # Prefer Redfish mains metrics when Redfish is the active connection
            if isinstance(self.connection, RedfishConnection):
                mains = await self.connection.get_mains_metrics()
                if mains:
                    if mains.get("voltage") is not None:
                        self.rms_voltage = mains["voltage"]
                    if mains.get("current") is not None:
                        self.rms_current = mains["current"]
                    if mains.get("frequency") is not None:
                        self.line_frequency = mains["frequency"]
                    if mains.get("power") is not None:
                        self.active_power = mains["power"]
                    if mains.get("energy") is not None:
                        # Convert kWh to Wh for internal consistency
                        self.active_energy = float(mains["energy"]) * 1000.0
                    if mains.get("apparent_power") is not None:
                        self.apparent_power = mains["apparent_power"]
                    if mains.get("power_factor") is not None:
                        self.power_factor = mains["power_factor"]
            else:
                # Legacy Telnet metrics path
                now = asyncio.get_event_loop().time()
                need_telnet_metrics = (
                    now - self._last_telnet_metrics_fetch
                ) >= self._telnet_metrics_interval_s
                if need_telnet_metrics and telnet_conn:
                    await asyncio.sleep(1.0)
                    inlet_response = await telnet_conn.send_command("show inlets all")
                    if inlet_response and "Unknown command" not in inlet_response:
                        current_match = re.search(
                            r"RMS Current:\s+([\d.]+)\s*A", inlet_response
                        )
                        if current_match:
                            self.rms_current = float(current_match.group(1))
                    outlet_response = await telnet_conn.send_command(
                        "show outlets 1 details"
                    )
                    if outlet_response and "Unknown command" not in outlet_response:
                        voltage_match = re.search(
                            r"RMS Voltage:\s+([\d.]+)\s*V", outlet_response
                        )
                        if voltage_match:
                            self.rms_voltage = float(voltage_match.group(1))
                        freq_match = re.search(
                            r"Line Frequency:\s+([\d.]+)\s*Hz", outlet_response
                        )
                        if freq_match:
                            self.line_frequency = float(freq_match.group(1))
                    self._last_telnet_metrics_fetch = now

                # Approximate total power for telnet devices without a
                # dedicated power reading
                if self.rms_voltage is not None and self.rms_current is not None:
                    self.active_power = self.rms_voltage * self.rms_current

            # Single loadshedding query per cycle (if telnet available)
            if telnet_conn:
                shedding_response = await telnet_conn.send_command("show loadshedding")
                if shedding_response:
                    self.load_shedding_active = "Active" in shedding_response
                    self._parse_and_set_non_critical_from_loadshedding(
                        shedding_response
                    )

        except Exception as err:
            _LOGGER.error("Error updating system status: %s", err)

    def _parse_and_set_non_critical_from_loadshedding(self, response: str) -> None:
        """Parse loadshedding response to set outlet_non_critical flags."""
        try:
            non_critical_outlets = self._parse_non_critical_outlets(response)
            if non_critical_outlets is None:
                return

            for outlet_num in self.outlet_states:
                self.outlet_non_critical[outlet_num] = (
                    outlet_num in non_critical_outlets
                )
            _LOGGER.debug(
                "Updated non-critical flags from device: %s",
                self.outlet_non_critical,
            )
        except Exception as err:
            _LOGGER.debug(
                "Failed to parse loadshedding response for non-critical flags: %s",
                err,
            )

    @staticmethod
    def _parse_non_critical_outlets(response: str) -> Optional[set]:
        """Extract non-critical outlet numbers from a loadshedding response.

        Expected format: "Non Critical Outlets: 3, 6-8"

        Returns None when the response does not contain the expected line.
        """
        for line in response.split("\n"):
            if "Non Critical Outlets:" not in line:
                continue
            non_critical_outlets: set = set()
            outlet_part = line.split(":", 1)[1].strip()
            for item in outlet_part.split(","):
                item = item.strip()
                if "-" in item:
                    start, end = map(int, item.split("-"))
                    non_critical_outlets.update(range(start, end + 1))
                elif item.isdigit():
                    non_critical_outlets.add(int(item))
            return non_critical_outlets
        return None

    async def _update_redfish_outlet_data(self) -> None:
        """Update individual outlet power data via Redfish API."""
        if not isinstance(self.connection, RedfishConnection):
            return

        # Try proper Redfish outlet metrics first
        metrics_map = await self.connection.get_all_outlets_metrics()
        if metrics_map:
            self.per_outlet_metrics_available = True
            for outlet_num, metrics in metrics_map.items():
                if outlet_num not in self.outlet_states:
                    continue
                if metrics.get("power") is not None:
                    self.outlet_power_data[outlet_num] = float(metrics["power"])
                if metrics.get("current") is not None:
                    self.outlet_current_data[outlet_num] = float(metrics["current"])
                if metrics.get("voltage") is not None:
                    self.outlet_voltage_data[outlet_num] = float(metrics["voltage"])
                if metrics.get("energy_kwh") is not None:
                    # Store Wh internally for consistency
                    try:
                        self.outlet_energy_data[outlet_num] = (
                            float(metrics["energy_kwh"]) * 1000.0
                        )
                    except (TypeError, ValueError):
                        pass

    async def turn_outlet_on(self, outlet: int) -> bool:
        """Turn an outlet on using appropriate protocol."""
        return await self._set_outlet_state(outlet, True)

    async def turn_outlet_off(self, outlet: int) -> bool:
        """Turn an outlet off using appropriate protocol."""
        return await self._set_outlet_state(outlet, False)

    async def _set_outlet_state(self, outlet: int, state: bool) -> bool:
        """Set an outlet state using the appropriate protocol."""
        state_name = "ON" if state else "OFF"
        try:
            _LOGGER.debug("Turning outlet %d %s", outlet, state_name)

            if isinstance(self.connection, RedfishConnection):
                success = await self.connection.set_outlet_state(outlet, state)
            elif isinstance(self.connection, SocketConnection):
                if self.connection.protocol_type == "telnet":
                    success = await self.connection.telnet_outlet_command(
                        outlet, "on" if state else "off"
                    )
                else:
                    success = await self.connection.send_outlet_command(
                        outlet, OUTLET_ON if state else OUTLET_OFF
                    )
            else:
                _LOGGER.error("No connection available")
                return False

            if success:
                _LOGGER.debug("Successfully turned outlet %d %s", outlet, state_name)
                self.outlet_states[outlet] = state
                return True

            _LOGGER.warning("Failed to turn outlet %d %s", outlet, state_name)
            return False

        except Exception as err:
            _LOGGER.error(
                "Error turning outlet %d %s: %s", outlet, state_name, err
            )
            return False

    async def cycle_outlet(self, outlet: int, cycle_time: int = 5) -> bool:
        """Cycle an outlet using appropriate protocol."""
        try:
            _LOGGER.debug("Cycling outlet %d for %d seconds", outlet, cycle_time)

            if isinstance(self.connection, RedfishConnection):
                success = await self.connection.cycle_outlet(outlet)
            elif isinstance(self.connection, SocketConnection):
                if self.connection.protocol_type == "telnet":
                    success = await self.connection.telnet_outlet_command(
                        outlet, "cycle"
                    )
                else:
                    success = await self.connection.send_outlet_command(
                        outlet, OUTLET_CYCLE, cycle_time
                    )
            else:
                _LOGGER.error("No connection available")
                return False

            if success:
                _LOGGER.debug("Successfully cycled outlet %d", outlet)
                # Wait briefly for the cycle to complete; the outlet ends up on
                await asyncio.sleep(cycle_time + 1)
                self.outlet_states[outlet] = True
                return True

            _LOGGER.warning("Failed to cycle outlet %d", outlet)
            return False

        except Exception as err:
            _LOGGER.error("Error cycling outlet %d: %s", outlet, err)
            return False

    async def cycle_all_outlets(self, cycle_time: int = 5) -> bool:
        """Cycle all outlets."""
        try:
            _LOGGER.debug("Cycling all outlets (%d outlets)", len(self.outlet_states))

            success_count = 0
            total_outlets = len(self.outlet_states)

            for outlet_num in sorted(self.outlet_states.keys()):
                if isinstance(self.connection, RedfishConnection):
                    success = await self.connection.cycle_outlet(outlet_num)
                else:
                    cmd = f"power outlets {outlet_num} cycle /y"
                    response = await self.connection.send_command(cmd)
                    # Empty response or a response without error indicators
                    # means the command was accepted
                    success = not response.strip() or not any(
                        indicator in response.lower()
                        for indicator in ("unknown command", "invalid", "error")
                    )

                if success:
                    success_count += 1
                else:
                    _LOGGER.warning("Failed to cycle outlet %d", outlet_num)

                # Small delay between commands to prevent session issues
                await asyncio.sleep(0.5)

            if success_count == 0:
                _LOGGER.warning("Failed to cycle any outlets")
                return False

            _LOGGER.info(
                "Successfully cycled %d of %d outlets", success_count, total_outlets
            )
            # Wait for the cycles to complete
            await asyncio.sleep(cycle_time + 2)
            for outlet_num in self.outlet_states:
                self.outlet_states[outlet_num] = True
            return success_count == total_outlets

        except Exception as err:
            _LOGGER.error("Error cycling all outlets: %s", err)
            return False

    async def start_load_shedding(self) -> bool:
        """Start load shedding using the device's native command."""
        return await self._set_load_shedding(True)

    async def stop_load_shedding(self) -> bool:
        """Stop load shedding using the device's native command."""
        return await self._set_load_shedding(False)

    async def _set_load_shedding(self, start: bool) -> bool:
        """Start or stop load shedding via the telnet channel."""
        action = "start" if start else "stop"
        try:
            telnet_conn = self._get_telnet_connection()
            if not telnet_conn:
                _LOGGER.error(
                    "No telnet connection available for load shedding "
                    "(vendor features %s)",
                    "enabled" if self.enable_vendor_features else "disabled",
                )
                return False

            response = await telnet_conn.send_command(f"loadshedding {action} /y")
            _LOGGER.debug("Load shedding %s response: %r", action, response)

            # Empty response is typical for successful commands
            success = not any(
                indicator in response.lower()
                for indicator in ("error", "failed", "unknown command")
            )

            if success:
                _LOGGER.info("Load shedding %s successful", action)
                self.load_shedding_active = start
                await self.update_outlet_non_critical_flags()
                return True

            _LOGGER.error("Failed to %s load shedding: %s", action, response)
            return False

        except Exception as err:
            _LOGGER.error("Error during load shedding %s: %s", action, err)
            return False

    async def start_sequence(self) -> bool:
        """Start outlet sequencing using config mode commands."""
        telnet_conn = self._get_telnet_connection()
        if not telnet_conn:
            _LOGGER.error("No telnet connection available for sequencing")
            return False

        try:
            _LOGGER.debug("Configuring outlet startup sequence")

            # Enter config mode
            await telnet_conn.send_command("config")
            await asyncio.sleep(0.5)

            try:
                outlet_list = sorted(self.outlet_states.keys())
                sequence_order = ",".join(str(outlet) for outlet in outlet_list)

                sequence_cmd = f"pdu outletSequence {sequence_order}"
                response = await telnet_conn.send_command(sequence_cmd)

                # Set a 2-second delay between each outlet
                delay_cmd = (
                    "pdu outletSequenceDelay "
                    + ";".join(f"{outlet}:2" for outlet in outlet_list)
                )
                delay_response = await telnet_conn.send_command(delay_cmd)

                apply_response = await telnet_conn.send_command("apply")
                _LOGGER.debug("Apply response: %r", apply_response)

                success = not any(
                    indicator in text.lower()
                    for text in (response, delay_response)
                    for indicator in ("error", "unknown command")
                )

                if success:
                    _LOGGER.info("Successfully configured outlet sequence")
                    self.sequence_active = True
                    return True

                _LOGGER.warning(
                    "Failed to configure sequence. Response: %r, Delay: %r",
                    response,
                    delay_response,
                )
                return False

            finally:
                # Always exit config mode
                await telnet_conn.send_command("cancel")
                await asyncio.sleep(0.5)

        except Exception as err:
            _LOGGER.error("Error configuring sequence: %s", err)
            return False

    async def stop_sequence(self) -> bool:
        """Disable outlet sequencing using config mode commands."""
        telnet_conn = self._get_telnet_connection()
        if not telnet_conn:
            _LOGGER.error("No telnet connection available for sequencing")
            return False

        try:
            _LOGGER.debug("Disabling outlet sequence (setting to default)")

            await telnet_conn.send_command("config")
            await asyncio.sleep(0.5)

            try:
                response = await telnet_conn.send_command("pdu outletSequence default")

                outlet_list = sorted(self.outlet_states.keys())
                delay_cmd = (
                    "pdu outletSequenceDelay "
                    + ";".join(f"{outlet}:0" for outlet in outlet_list)
                )
                delay_response = await telnet_conn.send_command(delay_cmd)

                apply_response = await telnet_conn.send_command("apply")
                _LOGGER.debug("Apply response: %r", apply_response)

                success = not any(
                    indicator in text.lower()
                    for text in (response, delay_response)
                    for indicator in ("error", "unknown command")
                )

                if success:
                    _LOGGER.info("Successfully disabled outlet sequence")
                    self.sequence_active = False
                    return True

                _LOGGER.warning(
                    "Failed to disable sequence. Response: %r, Delay: %r",
                    response,
                    delay_response,
                )
                return False

            finally:
                await telnet_conn.send_command("cancel")
                await asyncio.sleep(0.5)

        except Exception as err:
            _LOGGER.error("Error disabling sequence: %s", err)
            return False

    async def set_outlet_name(self, outlet: int, name: str) -> bool:
        """Set the user label of an outlet."""
        try:
            if isinstance(self.connection, RedfishConnection):
                if await self.connection.set_outlet_label(outlet, name):
                    self.outlet_names[outlet] = name
                    return True
                return False

            telnet_conn = self._get_telnet_connection()
            if not telnet_conn:
                _LOGGER.error("No connection available to rename outlet")
                return False

            await telnet_conn.send_command("config")
            await asyncio.sleep(0.5)
            try:
                response = await telnet_conn.send_command(
                    f'outlet {outlet} name "{name}"'
                )
                await telnet_conn.send_command("apply")
                success = not any(
                    indicator in response.lower()
                    for indicator in ("error", "unknown command", "invalid")
                )
                if success:
                    self.outlet_names[outlet] = name
                return success
            finally:
                await telnet_conn.send_command("cancel")
                await asyncio.sleep(0.5)

        except Exception as err:
            _LOGGER.error("Error setting outlet %d name: %s", outlet, err)
            return False

    async def set_pdu_name(self, name: str) -> bool:
        """Set the PDU display name."""
        try:
            if isinstance(self.connection, RedfishConnection):
                if await self.connection.set_pdu_name(name):
                    self.pdu_name = name
                    return True
                return False

            telnet_conn = self._get_telnet_connection()
            if not telnet_conn:
                _LOGGER.error("No connection available to rename PDU")
                return False

            await telnet_conn.send_command("config")
            await asyncio.sleep(0.5)
            try:
                response = await telnet_conn.send_command(f'pdu name "{name}"')
                await telnet_conn.send_command("apply")
                success = not any(
                    indicator in response.lower()
                    for indicator in ("error", "unknown command", "invalid")
                )
                if success:
                    self.pdu_name = name
                return success
            finally:
                await telnet_conn.send_command("cancel")
                await asyncio.sleep(0.5)

        except Exception as err:
            _LOGGER.error("Error setting PDU name: %s", err)
            return False

    async def update_outlet_non_critical_flags(self) -> None:
        """Update outlet non-critical flags from the device's load shedding status."""
        try:
            telnet_conn = self._get_telnet_connection()
            if not telnet_conn:
                _LOGGER.debug("No telnet connection available for load shedding status")
                return

            response = await telnet_conn.send_command("show loadshedding")

            if not response or "error" in response.lower():
                _LOGGER.debug("Could not get load shedding status")
                return

            self._parse_and_set_non_critical_from_loadshedding(response)

        except Exception as err:
            _LOGGER.error("Error updating non-critical flags: %s", err)
