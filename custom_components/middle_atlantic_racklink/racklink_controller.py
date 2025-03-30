"""Controller for Middle Atlantic Racklink PDU devices."""

import asyncio
import logging
import re
import socket
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .const import COMMAND_TIMEOUT, SUPPORTED_MODELS

_LOGGER = logging.getLogger(__name__)

# Model capabilities dictionary
MODEL_CAPABILITIES = {
    # RackLink Premium+ Series with RackLink
    "RLNK-P415": {"num_outlets": 4, "has_current_sensing": True},
    "RLNK-P420": {"num_outlets": 4, "has_current_sensing": True},
    "RLNK-P915R": {"num_outlets": 8, "has_current_sensing": True},
    "RLNK-P915R-SP": {"num_outlets": 8, "has_current_sensing": True},
    "RLNK-P920R": {"num_outlets": 8, "has_current_sensing": True},
    "RLNK-P920R-SP": {"num_outlets": 8, "has_current_sensing": True},
}


class RacklinkController:
    """Controller for Middle Atlantic RackLink devices."""

    def __init__(
        self,
        host: str,
        port: int = 6000,
        username: str = None,
        password: str = None,
        scan_interval: int = 30,
        socket_timeout: float = 5.0,
        login_timeout: float = 10.0,
        command_timeout: float = 5.0,
    ):
        """Initialize the Racklink controller."""
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._scan_interval = scan_interval
        self._socket_timeout = socket_timeout
        self._login_timeout = login_timeout
        self._command_timeout = command_timeout
        self._socket = None
        self._receive_buffer = b""
        self._socket_lock = asyncio.Lock()
        self._command_queue = asyncio.Queue()
        self._connected = False
        self._connect_lock = asyncio.Lock()
        self._last_connection_attempt = None
        self._last_error = None
        self._last_error_time = None
        self._connection_task = None
        self._command_processor_task = None
        self._connecting = False
        self._command_locks = {}

        # PDU info
        self._pdu_serial = None
        self._pdu_name = None
        self._pdu_model = None
        self._pdu_firmware = None
        self._mac_address = None

        # Outlet state tracking
        self._outlet_states = {}
        self._outlet_names = {}
        self._outlet_power = {}
        self._outlet_current = {}
        self._outlet_energy = {}
        self._outlet_voltage = {}
        self._outlet_power_factor = {}
        self._outlet_non_critical = {}
        self._outlet_apparent_power = {}
        self._outlet_line_frequency = {}

        # Sensor data
        self._sensors = {}

        # Device info
        self._pdu_info = {}

        _LOGGER.debug(
            "Initialized RackLink controller for %s:%s (username: %s)",
            host,
            port,
            username,
        )

    @property
    def connected(self) -> bool:
        """Return if we are connected to the device."""
        # Only consider truly connected if we have a socket object and _connected flag
        return self._connected and self._socket is not None

    @property
    def available(self) -> bool:
        """Return if the device is available."""
        return self._available

    @property
    def last_error(self) -> Optional[str]:
        """Return the last error message."""
        return self._last_error

    @property
    def last_error_time(self) -> Optional[datetime]:
        """Return the time of the last error."""
        return self._last_error_time

    @property
    def pdu_serial(self) -> Optional[str]:
        """Return the PDU serial number."""
        return self._pdu_serial

    @property
    def pdu_name(self) -> Optional[str]:
        """Return the PDU name."""
        return self._pdu_name

    @property
    def pdu_model(self) -> Optional[str]:
        """Return the PDU model."""
        return self._pdu_model

    @property
    def pdu_firmware(self) -> Optional[str]:
        """Return the PDU firmware version."""
        return self._pdu_firmware

    @property
    def mac_address(self) -> Optional[str]:
        """Return the PDU MAC address."""
        return self._mac_address

    @property
    def outlet_states(self) -> dict:
        """Return all outlet states."""
        return self._outlet_states

    @property
    def outlet_names(self) -> dict:
        """Return all outlet names."""
        return self._outlet_names

    @property
    def outlet_power(self) -> dict:
        """Return outlet power data."""
        return self._outlet_power

    @property
    def outlet_current(self) -> dict:
        """Return outlet current data."""
        return self._outlet_current

    @property
    def outlet_energy(self) -> dict:
        """Return outlet energy data."""
        return self._outlet_energy

    @property
    def outlet_voltage(self) -> dict:
        """Return outlet voltage data."""
        return self._outlet_voltage

    @property
    def outlet_power_factor(self) -> dict:
        """Return outlet power factor data."""
        return self._outlet_power_factor

    @property
    def outlet_non_critical(self) -> dict:
        """Return outlet non-critical status."""
        return self._outlet_non_critical

    @property
    def sensors(self) -> dict:
        """Return PDU sensor data."""
        return self._sensors

    @property
    def pdu_info(self) -> dict:
        """Return PDU information."""
        # Ensure PDU info is up to date
        if not self._pdu_info and (self._pdu_model or self._pdu_serial):
            self._pdu_info = {
                "model": self._pdu_model,
                "firmware": self._pdu_firmware,
                "serial": self._pdu_serial,
                "name": self._pdu_name,
                "mac_address": self._mac_address,
            }
        return self._pdu_info

    @property
    def outlet_apparent_power(self) -> dict:
        """Return the apparent power usage of each outlet."""
        return self._outlet_apparent_power

    @property
    def outlet_line_frequency(self) -> dict:
        """Return the line frequency of each outlet."""
        return self._outlet_line_frequency

    def _handle_error(self, error: str) -> None:
        """Handle an error by logging it and updating state."""
        self._last_error = error
        self._last_error_time = datetime.now(timezone.utc)
        self._available = False
        _LOGGER.error("Racklink error: %s", error)

    async def _create_socket_connection(self) -> Optional[socket.socket]:
        """Create a socket connection to the device."""
        loop = asyncio.get_event_loop()

        _LOGGER.debug("Creating socket connection to %s:%s", self._host, self._port)

        try:
            # Create a standard socket in non-blocking mode
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setblocking(False)

            # Set socket options
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

            # Connect using the event loop with timeout
            await loop.sock_connect(sock, (self._host, self._port))

            _LOGGER.debug(
                "Socket connection established to %s:%s", self._host, self._port
            )
            return sock

        except socket.timeout:
            _LOGGER.error(
                "Socket connection to %s:%s timed out", self._host, self._port
            )
            return None
        except ConnectionRefusedError:
            _LOGGER.error(
                "Connection refused to %s:%s - check if device is online and port is correct",
                self._host,
                self._port,
            )
            return None
        except socket.gaierror:
            _LOGGER.error(
                "Could not resolve hostname %s - check network settings", self._host
            )
            return None
        except OSError as e:
            if e.errno == 113:  # No route to host
                _LOGGER.error(
                    "No route to host %s - check network connectivity", self._host
                )
            else:
                _LOGGER.error(
                    "Socket error connecting to %s:%s: %s",
                    self._host,
                    self._port,
                    str(e),
                )
            return None
        except Exception as e:
            _LOGGER.error(
                "Unexpected error creating socket connection to %s:%s: %s",
                self._host,
                self._port,
                str(e),
            )
            return None

    async def _socket_read(self, timeout: float = 1.0) -> bytes:
        """Read data from the socket with a timeout."""
        if not self._socket:
            _LOGGER.debug("Cannot read from socket: socket not connected")
            return b""

        loop = asyncio.get_event_loop()

        try:
            # First check if socket is still valid
            if self._socket.fileno() == -1:
                _LOGGER.warning("Socket is no longer valid (fileno=-1)")
                self._connected = False
                self._available = False
                return b""

            # Create a Future that will receive the data
            read_future = loop.create_future()

            # Define the read callback to be used with add_reader
            def _socket_read_callback():
                if read_future.done():
                    return  # Avoid calling set_result twice

                try:
                    data = self._socket.recv(4096)
                    if data:
                        _LOGGER.debug("Read %d bytes from socket", len(data))
                        read_future.set_result(data)
                    else:
                        # Empty data means the connection was closed
                        _LOGGER.debug(
                            "Socket closed by remote host (received empty data)"
                        )
                        read_future.set_exception(
                            ConnectionError("Connection closed by remote host")
                        )
                        self._connected = False
                except BlockingIOError:
                    # No data available yet
                    pass
                except ConnectionError as e:
                    _LOGGER.debug("Socket connection error: %s", e)
                    self._connected = False
                    if not read_future.done():
                        read_future.set_exception(e)
                except OSError as e:
                    _LOGGER.debug("Socket OS error: %s", e)
                    self._connected = False
                    if not read_future.done():
                        read_future.set_exception(e)
                except Exception as e:
                    _LOGGER.debug("Socket read error: %s", e)
                    if not read_future.done():
                        read_future.set_exception(e)

            # Try to add the socket reader safely
            try:
                loop.add_reader(self._socket.fileno(), _socket_read_callback)
            except (ValueError, OSError) as e:
                _LOGGER.warning("Could not add socket reader: %s", e)
                self._connected = False
                return b""

            try:
                # Wait for the read to complete or timeout
                return await asyncio.wait_for(read_future, timeout=timeout)
            finally:
                # Always try to remove the reader when done
                try:
                    if self._socket and self._socket.fileno() != -1:
                        loop.remove_reader(self._socket.fileno())
                except (ValueError, OSError):
                    # Socket might be closed already
                    pass

        except asyncio.TimeoutError:
            _LOGGER.debug("Socket read timed out after %s seconds", timeout)
            return b""
        except ConnectionError as e:
            _LOGGER.debug("Connection error during socket read: %s", e)
            self._connected = False
            return b""
        except Exception as e:
            _LOGGER.error("Error reading from socket: %s", e)
            return b""

    async def _socket_write(self, data: bytes) -> None:
        """Write to socket connection in a non-blocking way."""
        if not self._socket:
            _LOGGER.error("Cannot write to socket: connection is None")
            raise ConnectionError("No socket connection available")

        if data is None:
            raise ValueError("Data cannot be None")

        try:
            _LOGGER.debug("Writing %d bytes to socket", len(data))
            # Safety check - don't use a direct reference to self._socket that could become None
            socket_connection = self._socket
            if socket_connection is None:
                raise ConnectionError("Socket connection became None")

            # Send data in a separate thread
            def send_data(sock, data_to_send):
                try:
                    sock.sendall(data_to_send)
                    return True
                except Exception as e:
                    _LOGGER.error("Socket send error: %s", e)
                    return False

            success = await asyncio.to_thread(send_data, socket_connection, data)

            if not success:
                raise ConnectionError("Failed to send data through socket")

        except asyncio.CancelledError:
            _LOGGER.warning("Socket write operation was cancelled")
            raise
        except AttributeError as exc:
            self._connected = False
            self._available = False
            raise ConnectionError(f"Socket connection lost: {exc}") from exc
        except Exception as exc:
            self._connected = False
            self._available = False
            raise ConnectionError(f"Error writing to socket: {exc}") from exc

    async def start_background_connection(self) -> None:
        """Start background connection task that won't block Home Assistant startup."""
        if self._connection_task is not None and not self._connection_task.done():
            _LOGGER.debug("Background connection task already running")
            return

        _LOGGER.debug("Starting background connection for %s", self._host)
        self._connection_task = asyncio.create_task(self._background_connect())

    async def _background_connect(self) -> bool:
        """Try to connect in the background."""
        if self._connected and self._socket:
            return True

        _LOGGER.debug(
            "Starting background connection attempt to %s:%s", self._host, self._port
        )

        # First try the regular connection method
        if await self.connect():
            _LOGGER.info("Background connection successful")
            return True

        # If standard connection failed, try the reconnect method which attempts alternative ports
        if await self.reconnect():
            _LOGGER.info(
                "Background connection successful using alternative connection method"
            )
            return True

        # If both methods failed, schedule a future reconnect attempt
        _LOGGER.warning("Background connection failed, scheduling delayed reconnect")
        self._schedule_reconnect()
        return False

    def _schedule_reconnect(self):
        """Schedule a reconnection with exponential backoff."""
        if not hasattr(self, "_retry_count"):
            self._retry_count = 0

        self._retry_count += 1

        # Calculate backoff delay with max of 5 minutes (300 seconds)
        delay = min(300, 2 ** min(self._retry_count, 8))

        _LOGGER.info("Will attempt to reconnect to %s in %s seconds", self._host, delay)

        # Schedule reconnection
        asyncio.create_task(self._delayed_reconnect(delay))

    async def _delayed_reconnect(self, delay):
        """Reconnect after a delay."""
        _LOGGER.debug("Scheduled reconnection to %s in %s seconds", self._host, delay)
        try:
            await asyncio.sleep(delay)

            # Only reconnect if we're still not connected
            if not self._connected:
                _LOGGER.debug("Attempting scheduled reconnection to %s", self._host)

                # Try to connect with a reasonable timeout
                try:
                    await asyncio.wait_for(
                        self._background_connect(), timeout=self._login_timeout
                    )
                except asyncio.TimeoutError:
                    _LOGGER.error("Scheduled reconnection to %s timed out", self._host)
                    self._schedule_reconnect()  # Try again with increased backoff
                except Exception as e:
                    _LOGGER.error(
                        "Error in scheduled reconnection to %s: %s", self._host, e
                    )
                    self._schedule_reconnect()  # Try again with increased backoff
            else:
                _LOGGER.debug("Already connected, skipping scheduled reconnection")
        except asyncio.CancelledError:
            _LOGGER.debug("Delayed reconnection task was cancelled")
        except Exception as e:
            _LOGGER.error("Error in delayed reconnection: %s", e)
            self._schedule_reconnect()  # Try again with increased backoff

    async def connect(self) -> bool:
        """Connect to the device."""
        if self._connected:
            return True

        async with self._connect_lock:
            try:
                _LOGGER.info(
                    "Connecting to %s:%s as %s", self._host, self._port, self._username
                )

                # Create socket connection
                self._socket = await self._create_socket_connection()
                if not self._socket:
                    _LOGGER.error("Failed to create socket connection")
                    self._handle_error("Failed to create socket connection")
                    return False

                # Log in to the device
                login_success = await self._login()
                if not login_success:
                    _LOGGER.error("Login failed")
                    self._handle_error("Login failed")
                    await self._close_socket()
                    return False

                # Mark as connected
                self._connected = True
                self._available = True
                _LOGGER.info("Successfully connected to %s:%s", self._host, self._port)

                # Initialize command processor if needed
                self._ensure_command_processor_running()

                # Discover valid commands on this device to help with command mapping
                try:
                    _LOGGER.debug("Initiating command discovery to learn device syntax")
                    await self.discover_valid_commands()
                except Exception as e:
                    _LOGGER.warning("Command discovery failed: %s", e)

                # Load initial data
                try:
                    await self._load_initial_data()
                except Exception as e:
                    _LOGGER.warning("Could not load initial data: %s", e)

                return True

            except Exception as e:
                self._handle_error(f"Error connecting: {e}")
                if self._socket:
                    await self._close_socket()
                return False

    async def _load_initial_data(self):
        """Load initial data from the PDU."""
        _LOGGER.debug("Loading initial PDU data")
        try:
            # Get PDU information first
            await self.get_device_info()

            # Get basic help to understand available commands
            help_cmd = "help"
            help_response = await self.send_command(help_cmd)
            _LOGGER.debug("Help response: %s", help_response[:500])

            # Get power help for more specific command syntax
            power_help_cmd = "help power"
            power_help_response = await self.send_command(power_help_cmd)
            _LOGGER.debug("Power help response: %s", power_help_response[:500])

            # Try to discover valid commands for this device
            await self.discover_valid_commands()

            # Get additional data
            await self.get_all_outlet_states()
            await self.get_sensor_values()

        except Exception as e:
            _LOGGER.error("Error loading initial data: %s", e)
            return False

        return True

    def _normalize_model_name(self, model_string: str) -> str:
        """Normalize model name from various formats."""
        if not model_string:
            _LOGGER.warning("Empty model string, using default model RLNK-P920R")
            return "RLNK-P920R"

        # Clean up the model string
        model = model_string.strip().upper()

        # Check for known prefixes and patterns
        if model.startswith("RLNK-P"):
            # Already normalized format
            _LOGGER.debug("Model already in normalized format: %s", model)
            return model

        # Look for P9xx pattern in the model string
        p9_match = re.search(r"P9(\d+)R?", model, re.IGNORECASE)
        if p9_match:
            suffix = p9_match.group(1)
            normalized = f"RLNK-P9{suffix}R"
            _LOGGER.debug("Normalized P9 model: %s to %s", model, normalized)
            return normalized

        # Look for P4xx pattern in the model string
        p4_match = re.search(r"P4(\d+)", model, re.IGNORECASE)
        if p4_match:
            suffix = p4_match.group(1)
            normalized = f"RLNK-P4{suffix}"
            _LOGGER.debug("Normalized P4 model: %s to %s", model, normalized)
            return normalized

        # No pattern matches, use a safe default
        _LOGGER.warning(
            "Could not normalize model '%s', using default RLNK-P920R", model
        )
        return "RLNK-P920R"

    async def get_device_info(self) -> dict:
        """Get device information."""
        _LOGGER.debug("Getting device information for %s", self._host)

        if not self._connected:
            _LOGGER.debug("Not connected during get_device_info, attempting to connect")
            if not await self.reconnect():
                _LOGGER.warning("Could not connect to get device info")
                return {}

        # Only fetch device info if we don't have it yet
        if not self._pdu_model or not self._pdu_serial:
            _LOGGER.info("Fetching PDU details")

            try:
                # Import parser module here to avoid circular imports
                from .parser import (
                    parse_device_info,
                    parse_network_info,
                    normalize_model_name,
                )

                # Get PDU details
                details_cmd = "show pdu details"
                details_response = await self.send_command(details_cmd)

                if details_response:
                    # Use the parser to extract device info
                    device_info = parse_device_info(details_response)

                    # Update internal data from parsed results
                    if "name" in device_info:
                        self._pdu_name = device_info["name"]
                        _LOGGER.debug("Found PDU name: %s", self._pdu_name)

                    if "model" in device_info:
                        raw_model = device_info["model"]
                        self._pdu_model = normalize_model_name(raw_model)
                        _LOGGER.debug(
                            "Found PDU model: %s (normalized from %s)",
                            self._pdu_model,
                            raw_model,
                        )

                    if "serial" in device_info:
                        self._pdu_serial = device_info["serial"]
                        _LOGGER.debug("Found PDU serial: %s", self._pdu_serial)

                    if "firmware" in device_info:
                        self._pdu_firmware = device_info["firmware"]
                        _LOGGER.debug("Found PDU firmware: %s", self._pdu_firmware)

                # Get MAC address if we don't have it
                if not self._mac_address:
                    _LOGGER.debug("Getting network interface information")
                    net_cmd = "show network interface eth1"
                    net_response = await self.send_command(net_cmd)

                    if net_response:
                        network_info = parse_network_info(net_response)
                        if "mac_address" in network_info:
                            self._mac_address = network_info["mac_address"]
                            _LOGGER.debug(
                                "Found PDU MAC address: %s", self._mac_address
                            )
                    else:
                        # Try alternative interface name if eth1 failed
                        net_cmd = "show network interface eth0"
                        net_response = await self.send_command(net_cmd)

                        if net_response:
                            network_info = parse_network_info(net_response)
                            if "mac_address" in network_info:
                                self._mac_address = network_info["mac_address"]
                                _LOGGER.debug(
                                    "Found PDU MAC address: %s", self._mac_address
                                )

            except Exception as e:
                _LOGGER.error("Error getting device info: %s", e)
        else:
            _LOGGER.debug("Using cached device info")

        # Fill in defaults for missing data
        if not self._pdu_model:
            _LOGGER.warning("Could not determine PDU model, using default")
            self._pdu_model = "RLNK-P920R"  # Default model

        if not self._pdu_name:
            self._pdu_name = f"RackLink PDU {self._host}"

        if not self._pdu_serial:
            # Generate a pseudo-serial based on MAC if we have it, otherwise use host
            if self._mac_address:
                self._pdu_serial = f"UNKNOWN-{self._mac_address.replace(':', '')}"
            else:
                self._pdu_serial = f"UNKNOWN-{self._host.replace('.', '')}"

        # Update PDU info dictionary
        self._pdu_info = {
            "model": self._pdu_model or "Unknown",
            "firmware": self._pdu_firmware or "Unknown",
            "serial": self._pdu_serial or "Unknown",
            "name": self._pdu_name or "RackLink PDU",
            "mac_address": self._mac_address or "Unknown",
        }

        _LOGGER.debug("Device info: %s", self._pdu_info)
        return self._pdu_info

    def get_model_capabilities(self) -> Dict[str, Any]:
        """Get capabilities for the current model."""
        # Default capabilities
        capabilities = {
            "num_outlets": 8,  # Default to 8 outlets for this device
            "has_current_sensing": False,
            "supports_energy_monitoring": False,
            "supports_power_factor": False,
            "supports_temperature": False,
        }

        # Get model if available
        model = self._pdu_model

        # If we have a known model, use its specific capabilities
        if model and model in MODEL_CAPABILITIES:
            capabilities.update(MODEL_CAPABILITIES[model])
        else:
            # If model detection failed, use a safe default
            _LOGGER.debug(
                "Model '%s' not found in capabilities list, using defaults", model
            )
            # Hardcoded override for this specific device
            capabilities["num_outlets"] = 8

        # Force number of outlets for certain hosts
        if self._host == "10.0.1.211":
            # Override for this specific host
            capabilities["num_outlets"] = 8
            _LOGGER.debug("Forcing 8 outlets for specific host %s", self._host)

        # Get number of outlets with a safe default
        num_outlets = capabilities.get("num_outlets", 8)

        # Always log the capabilities
        _LOGGER.debug(
            "Model capabilities for %s: outlets=%d, current_sensing=%s",
            model,
            capabilities["num_outlets"],
            capabilities["has_current_sensing"],
        )

        return capabilities

    async def update(self) -> bool:
        """Update PDU state - called periodically by the coordinator."""
        try:
            if not self._connected:
                _LOGGER.debug("Not connected during update, attempting reconnection")
                reconnected = await self._handle_connection_issues()
                if not reconnected:
                    _LOGGER.warning("Failed to reconnect during update")
                    self._available = False
                    return False

            # Get basic device info if we don't have it
            if not self._pdu_model or not self._pdu_serial:
                await self.get_device_info()

            # Update all outlet states efficiently with a single command
            _LOGGER.debug("Updating all outlet states")
            try:
                await self.get_all_outlet_states(force_refresh=True)
            except Exception as e:
                _LOGGER.error("Error updating outlet states: %s", e)

            # Get sensor data
            try:
                await self.get_sensor_values(force_refresh=True)
            except Exception as e:
                _LOGGER.error("Error updating sensor values: %s", e)

            # Update detailed outlet metrics for a subset of outlets
            # to avoid overwhelming the device with too many commands
            if any(
                self.get_model_capabilities().get(feature, False)
                for feature in [
                    "supports_power_monitoring",
                    "supports_energy_monitoring",
                    "has_current_sensing",
                ]
            ):
                await self.get_all_power_data(sample_size=3)

            # Mark successful update
            self._available = True
            self._last_update = time.time()
            return True

        except Exception as e:
            _LOGGER.error("Error in update: %s", e)
            self._available = False
            return False

    async def get_all_power_data(self, sample_size: int = 3) -> bool:
        """Get power data for all outlets efficiently.

        Uses sampling to avoid overwhelming the device, fetching detailed data
        for a subset of outlets during each update cycle.
        """
        if not self._connected:
            _LOGGER.debug("Not connected, skipping power data refresh")
            return False

        try:
            # Get model capabilities
            capabilities = self.get_model_capabilities()
            num_outlets = capabilities.get("num_outlets", 8)

            # Determine which outlets to sample in this cycle
            all_outlets = list(range(1, num_outlets + 1))

            # Try to select outlets that don't have power data yet
            missing_power_data = [o for o in all_outlets if o not in self._outlet_power]

            # Decide which outlets to sample
            if len(missing_power_data) > 0:
                # Prioritize outlets missing data
                sample_outlets = missing_power_data[:sample_size]
            else:
                # Cycle through all outlets over time
                current_time = time.time()
                # Use time-based sampling to ensure all outlets get updated eventually
                start_idx = (
                    int(current_time / 30) % num_outlets
                )  # Rotate every 30 seconds
                indices = [(start_idx + i) % num_outlets for i in range(sample_size)]
                sample_outlets = [all_outlets[i] for i in indices]

            _LOGGER.debug("Sampling power data for outlets: %s", sample_outlets)

            success_count = 0
            for outlet in sample_outlets:
                try:
                    data = await self.get_outlet_power_data(outlet)
                    if data:
                        success_count += 1
                    # Brief delay to avoid overwhelming the device
                    await asyncio.sleep(0.2)
                except Exception as e:
                    _LOGGER.error(
                        "Error getting power data for outlet %d: %s", outlet, e
                    )

            _LOGGER.debug(
                "Got power data for %d of %d sampled outlets",
                success_count,
                len(sample_outlets),
            )

            return success_count > 0

        except Exception as e:
            _LOGGER.error("Error getting power data: %s", e)
            return False

    async def get_outlet_name(self, outlet_num: int) -> str:
        """Get the name of a specific outlet."""
        # If we already have the name cached, use it
        if outlet_num in self._outlet_names:
            return self._outlet_names[outlet_num]

        # Otherwise, try to get it from the device
        try:
            if not self._connected:
                await self._handle_connection_issues()
                if not self._connected:
                    return f"Outlet {outlet_num}"

            # Get outlet details
            command = f"show outlets {outlet_num} details"
            response = await self.send_command(command)

            if not response:
                return f"Outlet {outlet_num}"

            # Try to parse the outlet name
            # Look for the outlet header line which includes the name
            name_match = re.search(rf"Outlet {outlet_num}(?: - (.+?))?:", response)

            if name_match and name_match.group(1):
                name = name_match.group(1).strip()
                self._outlet_names[outlet_num] = name
                return name
            else:
                return f"Outlet {outlet_num}"

        except Exception as e:
            _LOGGER.error("Error getting outlet name: %s", e)
            return f"Outlet {outlet_num}"

    async def is_outlet_available(self, outlet_num: int) -> bool:
        """Check if an outlet exists and is available on this PDU."""
        if not self._connected:
            return False

        capabilities = self.get_model_capabilities()
        num_outlets = capabilities.get("num_outlets", 8)

        # Check if outlet number is valid
        if outlet_num < 1 or outlet_num > num_outlets:
            return False

        # If we have data for this outlet, it's available
        if outlet_num in self._outlet_states:
            return True

        # Try to get details for this outlet
        try:
            command = f"show outlets {outlet_num} details"
            response = await self.send_command(command)

            # If we get a valid response that includes the outlet number,
            # then it exists on this PDU
            if response and f"Outlet {outlet_num}" in response:
                return True
            else:
                return False
        except Exception:
            return False

    def _parse_outlet_details(self, response: str, outlet_num: int) -> dict:
        """
        Parse outlet details response to extract power metrics.

        DEPRECATED: This method is maintained for backward compatibility.
        Use the parser module's parse_outlet_details() function instead.
        """
        # Import parser here to avoid circular imports
        from .parser import parse_outlet_details

        # Use the dedicated parser function
        return parse_outlet_details(response, outlet_num)

    async def get_outlet_power_data(self, outlet_num: int) -> dict:
        """Get power data for a specific outlet."""
        if not self._connected:
            _LOGGER.warning("Not connected, cannot get outlet power data")
            return {}

        try:
            _LOGGER.debug("Getting power data for outlet %d", outlet_num)

            # Try standard command format
            cmd = f"power outlets status {outlet_num}"
            response = await self.send_command(cmd)

            if not response or "Unknown command" in response.lower():
                _LOGGER.warning(
                    "Could not get power data for outlet %d: %s", outlet_num, response
                )
                return {}

            # Import parser here to avoid circular imports
            from .parser import parse_outlet_details

            # Parse the data
            outlet_data = parse_outlet_details(response, outlet_num)

            # Update the internal data structures
            if "current" in outlet_data:
                self._outlet_current[outlet_num] = outlet_data["current"]
            if "voltage" in outlet_data:
                self._outlet_voltage[outlet_num] = outlet_data["voltage"]
            if "power" in outlet_data:
                self._outlet_power[outlet_num] = outlet_data["power"]
            if "energy" in outlet_data:
                self._outlet_energy[outlet_num] = outlet_data["energy"]
            if "power_factor" in outlet_data:
                self._outlet_power_factor[outlet_num] = outlet_data["power_factor"]
            if "apparent_power" in outlet_data:
                self._outlet_apparent_power[outlet_num] = outlet_data["apparent_power"]
            if "line_frequency" in outlet_data:
                self._outlet_line_frequency[outlet_num] = outlet_data["line_frequency"]
            if "non_critical" in outlet_data:
                self._outlet_non_critical[outlet_num] = outlet_data["non_critical"]

            return outlet_data
        except Exception as e:
            _LOGGER.error("Error getting outlet power data: %s", e)
            return {}

    async def _close_socket(self) -> None:
        """Close the current socket connection safely."""
        if self._socket is not None:
            _LOGGER.debug("Closing existing socket connection")
            try:
                # Close socket in a thread to avoid blocking
                def close_socket(sock):
                    try:
                        sock.close()
                    except Exception as close_err:
                        _LOGGER.debug("Error closing socket: %s", close_err)

                await asyncio.to_thread(close_socket, self._socket)
            except Exception as e:
                _LOGGER.debug("Error closing socket connection: %s", e)
            finally:
                self._socket = None
                self._connected = False
                self._available = False
                self._receive_buffer = b""  # Clear buffer on disconnect

    def _ensure_command_processor_running(self) -> None:
        """Ensure the command processor task is running."""
        if self._command_processor_task is None or self._command_processor_task.done():
            _LOGGER.debug("Starting command processor task")
            self._command_processor_task = asyncio.create_task(
                self._process_command_queue()
            )
            # Add a done callback to log when the task completes
            self._command_processor_task.add_done_callback(
                lambda fut: _LOGGER.debug(
                    "Command processor task completed: %s",
                    (
                        fut.exception()
                        if fut.done() and not fut.cancelled()
                        else "No exception"
                    ),
                )
            )

    async def _login(self) -> bool:
        """Login to the device if needed."""
        try:
            if not self._socket:
                _LOGGER.error("Cannot login: no socket connection")
                return False

            _LOGGER.debug("Starting login sequence for %s", self._host)

            # Step 1: Wait for username prompt
            _LOGGER.debug("Waiting for username prompt...")
            try:
                username_response = await self._socket_read_until(
                    b"Username:", timeout=self._login_timeout
                )
                if not username_response:
                    _LOGGER.error("Username prompt not detected")
                    return False
                _LOGGER.debug("Username prompt detected")
            except Exception as e:
                _LOGGER.error("Error waiting for username prompt: %s", e)
                return False

            # Step 2: Send username
            _LOGGER.debug("Sending username: %s", self._username)
            try:
                await self._socket_write(f"{self._username}\r\n".encode())
                # Give device time to process
                await asyncio.sleep(0.5)
            except Exception as e:
                _LOGGER.error("Error sending username: %s", e)
                return False

            # Step 3: Wait for password prompt
            _LOGGER.debug("Waiting for password prompt...")
            try:
                password_response = await self._socket_read_until(
                    b"Password:", timeout=self._login_timeout
                )
                if not password_response:
                    _LOGGER.error("Password prompt not detected")
                    return False
                _LOGGER.debug("Password prompt detected")
            except Exception as e:
                _LOGGER.error("Error waiting for password prompt: %s", e)
                return False

            # Step 4: Send password
            _LOGGER.debug("Sending password...")
            try:
                await self._socket_write(f"{self._password}\r\n".encode())
                # Give device time to process
                await asyncio.sleep(1)
            except Exception as e:
                _LOGGER.error("Error sending password: %s", e)
                return False

            # Step 5: Wait for command prompt
            _LOGGER.debug("Waiting for command prompt...")
            try:
                # Look specifically for the # prompt (most common for this device)
                command_response = await self._socket_read_until(
                    b"#", timeout=self._login_timeout
                )
                if not command_response:
                    _LOGGER.error("Command prompt not detected")
                    return False
                _LOGGER.debug("Command prompt detected - login successful")
            except Exception as e:
                _LOGGER.error("Error waiting for command prompt: %s", e)
                return False

            _LOGGER.info("Successfully logged in to %s", self._host)
            return True

        except Exception as e:
            _LOGGER.error("Unexpected error during login: %s", e)
            return False

    async def disconnect(self) -> bool:
        """Disconnect from the PDU - used by config_flow for validation."""
        try:
            await self._close_socket()
            return True
        except Exception as e:
            _LOGGER.error("Error disconnecting: %s", e)
            return False

    async def shutdown(self) -> None:
        """Gracefully shut down the controller and release resources."""
        _LOGGER.debug("Shutting down controller for %s", self._host)

        # Flag that we're shutting down so other tasks can exit cleanly
        self._shutdown_requested = True

        # Cancel the command processor task
        if self._command_processor_task and not self._command_processor_task.done():
            _LOGGER.debug("Cancelling command processor task")
            self._command_processor_task.cancel()
            try:
                # Wait briefly for cancellation
                await asyncio.wait_for(
                    asyncio.shield(self._command_processor_task), timeout=1
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        # Cancel any connection task
        if self._connection_task and not self._connection_task.done():
            _LOGGER.debug("Cancelling connection task")
            self._connection_task.cancel()
            try:
                # Wait briefly for cancellation
                await asyncio.wait_for(asyncio.shield(self._connection_task), timeout=1)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        # Close socket connection
        await self._close_socket()

        _LOGGER.info("Controller for %s has been shut down", self._host)

    def _map_standard_command(self, command: str) -> str:
        """Map a standard command to one that works with this device."""
        # First, check exact matches for standard commands
        if command == "show outlets all":
            return "power outlets status"

        # Handle show outlet details command for specific outlet
        if re.match(r"show outlets? (\d+) details?", command):
            outlet_num = re.match(r"show outlets? (\d+) details?", command).group(1)
            return f"power outlets status {outlet_num}"

        # Handle power commands with different syntax
        power_off_match = re.match(r"power outlets? (\d+) off", command)
        if power_off_match:
            outlet_num = power_off_match.group(1)
            return f"power outlets off {outlet_num}"

        power_on_match = re.match(r"power outlets? (\d+) on", command)
        if power_on_match:
            outlet_num = power_on_match.group(1)
            return f"power outlets on {outlet_num}"

        cycle_match = re.match(r"power outlets? (\d+) cycle", command)
        if cycle_match:
            outlet_num = cycle_match.group(1)
            return f"power outlets cycle {outlet_num}"

        # All outlets commands
        if command == "power outlets all on":
            return "power outlets on all"

        if command == "power outlets all off":
            return "power outlets off all"

        if command == "power outlets all cycle":
            return "power outlets cycle all"

        # Return original command if no mapping found
        return command

    async def send_command(
        self,
        command: str,
        timeout: int = None,
        wait_time: float = 0.5,
        retry_alternative_command: bool = True,
    ) -> str:
        """Send a command to the device and wait for the response."""
        if not command:
            return ""

        # Map standard command to device-specific format if needed
        mapped_command = self._map_standard_command(command)
        if mapped_command != command:
            _LOGGER.debug(
                "Using mapped command '%s' instead of '%s'", mapped_command, command
            )
            command = mapped_command

        command_timeout = timeout or self._command_timeout
        command_with_newline = f"{command}\r\n"

        # Retry counter for this specific command
        retry_count = 0
        max_retries = 2  # Maximum retries for a single command

        while retry_count <= max_retries:
            try:
                if not self._connected:
                    _LOGGER.debug("Not connected while sending command: %s", command)
                    # Only try to reconnect on the first attempt
                    if retry_count == 0:
                        connection_success = await self._handle_connection_issues()
                        if not connection_success:
                            _LOGGER.warning(
                                "Could not reconnect to send command: %s", command
                            )
                            return ""
                    else:
                        # Don't try to reconnect on subsequent retries
                        return ""

                _LOGGER.debug(
                    "Sending command: %s (timeout: %s, attempt: %d)",
                    command,
                    command_timeout,
                    retry_count + 1,
                )

                # Flush any data in the buffer before sending
                if self._receive_buffer:
                    _LOGGER.debug(
                        "Clearing read buffer of %d bytes before sending command",
                        len(self._receive_buffer),
                    )
                    self._receive_buffer = b""

                # Send the command
                try:
                    await self._socket_write(command_with_newline.encode())
                except ConnectionError as e:
                    _LOGGER.error("Connection error while sending command: %s", e)
                    # Mark as disconnected
                    self._connected = False
                    self._available = False
                    # Try again if we have retries left
                    retry_count += 1
                    continue

                # Wait for device to process the command
                # Some commands need more time, especially power control commands
                cmd_wait_time = wait_time
                if "power outlets" in command:
                    # Power commands need more time
                    cmd_wait_time = wait_time * 2

                await asyncio.sleep(cmd_wait_time)

                # Read response until we get the command prompt
                response = await self._socket_read_until(b"#", timeout=command_timeout)

                # If we lost connection during read, try again
                if not self._connected and retry_count < max_retries:
                    _LOGGER.warning("Lost connection while reading response, retrying")
                    retry_count += 1
                    continue

                # Convert to string for parsing
                response_text = response.decode("utf-8", errors="ignore")

                # Debug log the response
                if len(response_text) < 200:  # Only log small responses
                    _LOGGER.debug("Response for '%s': %s", command, response_text)
                else:
                    _LOGGER.debug(
                        "Response for '%s': %d bytes (showing first 100): %s...",
                        command,
                        len(response_text),
                        response_text[:100],
                    )

                # Check if the response contains an error about unrecognized command
                if (
                    "unrecognized" in response_text.lower()
                    or "invalid" in response_text.lower()
                ) and "argument" in response_text.lower():
                    _LOGGER.warning(
                        "Command '%s' not recognized by device: %s",
                        command,
                        response_text.split("\n")[0],
                    )

                    # If this is our first try and we're allowed to try alternative commands
                    if retry_count == 0 and retry_alternative_command:
                        # If we haven't discovered commands yet, do it now
                        if (
                            not hasattr(self, "_valid_commands")
                            or not self._valid_commands
                        ):
                            _LOGGER.info(
                                "Initiating command discovery to find valid syntax"
                            )
                            await self.discover_valid_commands()
                            # Try again with discovered commands
                            retry_count += 1
                            continue

                # Log more details about pattern matching to help with power state issues
                if "show outlets" in command and "details" in command:
                    _LOGGER.info(
                        "Detailed outlet command response length: %d bytes",
                        len(response_text),
                    )
                    # Extract outlet number for more contextual logging
                    outlet_match = re.search(r"show outlets (\d+)", command)
                    outlet_num = outlet_match.group(1) if outlet_match else "unknown"

                    # Log key patterns that might help with parsing
                    power_state_match = re.search(
                        r"Power state:\s*(\w+)", response_text, re.IGNORECASE
                    )
                    if power_state_match:
                        _LOGGER.info(
                            "Found 'Power state: %s' for outlet %s",
                            power_state_match.group(1),
                            outlet_num,
                        )
                    else:
                        _LOGGER.info(
                            "No 'Power state:' pattern found for outlet %s", outlet_num
                        )

                        # Look for alternative patterns in the response
                        status_match = re.search(
                            r"Status:\s*(\w+)", response_text, re.IGNORECASE
                        )
                        if status_match:
                            _LOGGER.info(
                                "Found alternative 'Status: %s' for outlet %s",
                                status_match.group(1),
                                outlet_num,
                            )

                        # Try to extract relevant context around the outlet mention
                        outlet_context = re.search(
                            r"Outlet\s+%s.*?(?:\n.*?){0,5}" % outlet_num,
                            response_text,
                            re.IGNORECASE | re.DOTALL,
                        )
                        if outlet_context:
                            context_text = outlet_context.group(0).replace("\n", " | ")
                            _LOGGER.info(
                                "Context for outlet %s: %s",
                                outlet_num,
                                context_text[:100],
                            )

                # Verify the response contains evidence of command execution
                # For safety, ensure we have a valid response before proceeding
                if not response_text or (
                    len(response_text) < 3 and "#" not in response_text
                ):
                    _LOGGER.warning(
                        "Potentially invalid response: '%s' for command: %s",
                        response_text,
                        command,
                    )

                    if retry_count < max_retries:
                        _LOGGER.debug(
                            "Retrying command: %s (attempt %d)",
                            command,
                            retry_count + 2,
                        )
                        retry_count += 1
                        # Wait before retrying
                        await asyncio.sleep(1)
                        continue

                # If we got here, we have a valid response or have exhausted retries
                return response_text

            except ConnectionError as e:
                _LOGGER.error("Connection error sending command '%s': %s", command, e)
                # Try to recover the connection only on first attempt
                if retry_count == 0:
                    await self._handle_connection_issues()
                retry_count += 1
                if retry_count <= max_retries:
                    await asyncio.sleep(1)  # Wait before retry
                    continue
                return ""

            except asyncio.TimeoutError:
                _LOGGER.error("Timeout sending command '%s'", command)
                retry_count += 1
                if retry_count <= max_retries:
                    await asyncio.sleep(1)  # Wait before retry
                    continue
                return ""

            except Exception as e:
                _LOGGER.error("Error sending command '%s': %s", command, e)
                retry_count += 1
                if retry_count <= max_retries:
                    await asyncio.sleep(1)  # Wait before retry
                    continue
                return ""

        # If we got here, we've exhausted all retries
        return ""

    async def _handle_connection_issues(self) -> bool:
        """Handle connection issues by attempting to reconnect.

        Returns True if successfully reconnected.
        """
        if not self._connected:
            _LOGGER.debug("Connection issue detected, attempting to reconnect")

            # Try immediate reconnect (this will try multiple ports)
            reconnect_success = await self.reconnect()

            if reconnect_success:
                _LOGGER.info("Successfully reconnected")
                return True
            else:
                _LOGGER.error("Failed to reconnect after multiple attempts")
                # Schedule a delayed reconnect with exponential backoff
                self._schedule_reconnect()
                return False
        return True  # Already connected

    async def _process_command_queue(self):
        """Process commands from the queue."""
        _LOGGER.debug("Starting command queue processor")
        consecutive_errors = 0

        while not self._shutdown_requested:
            try:
                # Check if we're connected
                if not self._connected:
                    reconnect_success = await self._handle_connection_issues()
                    if not reconnect_success:
                        # Wait longer if we couldn't reconnect
                        await asyncio.sleep(5)
                        continue
                    else:
                        # Successfully reconnected, reset error counter
                        consecutive_errors = 0

                # Try to get a command from the queue with a timeout
                try:
                    command_item = await asyncio.wait_for(
                        self._command_queue.get(), timeout=0.5
                    )
                except asyncio.TimeoutError:
                    # No commands in queue, continue the loop
                    await asyncio.sleep(0.1)
                    continue

                command, future = command_item
                _LOGGER.debug("Processing command from queue: %s", command)

                # Send the command and get response
                try:
                    # Try to send the command
                    response = await self.send_command(command)
                    consecutive_errors = 0  # Reset on success

                    # Set the result if we have a future
                    if future is not None and not future.done():
                        future.set_result(response)
                except Exception as e:
                    _LOGGER.error("Error executing queued command '%s': %s", command, e)
                    consecutive_errors += 1

                    # Set exception on future if we have one
                    if future is not None and not future.done():
                        future.set_exception(e)

                    # If we have multiple errors in a row, try reconnecting
                    if consecutive_errors >= 3:
                        _LOGGER.warning(
                            "Multiple consecutive command errors, attempting reconnect"
                        )
                        await self._handle_connection_issues()
                        consecutive_errors = 0
                finally:
                    # Mark task as done regardless of outcome
                    self._command_queue.task_done()

                # Small delay to prevent flooding the device
                # Use a longer delay after power commands
                if "power outlets" in command:
                    await asyncio.sleep(self._command_delay * 2)
                else:
                    await asyncio.sleep(self._command_delay)

            except asyncio.CancelledError:
                _LOGGER.debug("Command processor was cancelled")
                break
            except Exception as e:
                _LOGGER.error("Unexpected error in command processor: %s", e)
                consecutive_errors += 1

                # Try to reconnect if we have multiple errors
                if consecutive_errors >= 3:
                    await self._handle_connection_issues()
                    consecutive_errors = 0

                await asyncio.sleep(
                    1
                )  # Sleep briefly to avoid tight loop on persistent errors

        _LOGGER.debug(
            "Command processor exiting, shutdown requested: %s",
            self._shutdown_requested,
        )

        # If we're not shutting down, restart the processor
        if not self._shutdown_requested:
            _LOGGER.debug("Restarting command processor")
            self._command_processor_task = asyncio.create_task(
                self._process_command_queue()
            )

    async def reconnect(self) -> bool:
        """Reconnect to the device if not already connected."""
        async with self._connect_lock:
            if self._connected and self._socket:
                _LOGGER.debug("Already connected, no need to reconnect")
                return True

            _LOGGER.info("Attempting to reconnect to %s:%s", self._host, self._port)

            # Close any existing socket first
            await self._close_socket()

            # Save the original port for reference
            original_port = self._port

            # Try different port numbers if the default doesn't work
            potential_ports = [original_port]

            # If the port is the default (6000), also try some alternative common ports
            if original_port == 6000:
                potential_ports.extend([23, 22, 2000, 4000, 8000, 6001])

            # Try each port in sequence
            for port in potential_ports:
                if port != original_port:
                    _LOGGER.info("Trying alternative port %s for %s", port, self._host)

                self._port = port  # Set the current port we're trying

                try:
                    # Create socket with a reasonable timeout
                    self._socket = await asyncio.wait_for(
                        self._create_socket_connection(), timeout=5
                    )

                    if not self._socket:
                        _LOGGER.debug(
                            "Failed to reconnect on port %s - socket creation failed",
                            port,
                        )
                        continue  # Try next port

                    # Try to log in
                    login_success = await asyncio.wait_for(self._login(), timeout=5)

                    if login_success:
                        self._connected = True
                        self._available = True

                        # If this was an alternative port, log that we found a working port
                        if port != original_port:
                            _LOGGER.warning(
                                "Successfully connected to %s using alternative port %s instead of %s",
                                self._host,
                                port,
                                original_port,
                            )
                        else:
                            _LOGGER.info(
                                "Successfully reconnected to %s on port %s",
                                self._host,
                                port,
                            )

                        # Ensure command processor is running
                        self._ensure_command_processor_running()

                        # Update device info
                        await self.get_device_info()

                        return True
                    else:
                        _LOGGER.debug("Failed to log in on port %s", port)
                        await self._close_socket()

                except asyncio.TimeoutError:
                    _LOGGER.debug(
                        "Connection attempt to %s on port %s timed out",
                        self._host,
                        port,
                    )
                    await self._close_socket()
                except Exception as e:
                    _LOGGER.debug(
                        "Error during reconnection to %s on port %s: %s",
                        self._host,
                        port,
                        e,
                    )
                    await self._close_socket()

            # If we get here, we failed to connect on any port
            _LOGGER.error("Failed to reconnect to %s on any port", self._host)

            # Restore original port
            self._port = original_port
            return False

    async def _socket_read_until(
        self, pattern: bytes = None, timeout: float = None
    ) -> bytes:
        """Read from socket connection until pattern is found."""
        if not self._socket:
            _LOGGER.debug("Cannot read until pattern: socket not connected")
            return b""

        if pattern is None:
            pattern = b"#"  # Change default prompt to # which is what the device uses

        timeout = timeout or self._socket_timeout
        start_time = time.time()

        # Import parser for command prompt detection
        from .parser import is_command_prompt

        # Support specific RackLink prompt patterns based on the response samples
        patterns = [pattern]
        if pattern == b"#":
            # Add command prompt pattern with bracket: [DeviceName] #
            patterns.extend([rb"]\s+#", b">", b"$", b":", b"RackLink>", b"admin>"])
        elif pattern == b"Username:":
            patterns = [b"Username:", b"login:"]  # Username prompts
        elif pattern == b"Password:":
            patterns = [b"Password:", b"password:"]  # Password prompts

        # Use existing buffer or create a new one
        buffer = self._receive_buffer

        # Add a more sophisticated timeout handling with partial matches
        max_attempts = 20
        attempt_count = 0
        last_buffer_size = len(buffer)
        last_data_time = time.time()

        while time.time() - start_time < timeout:
            # Check if any pattern is already in buffer
            for ptn in patterns:
                if ptn in buffer:
                    # Special check for bracket pattern
                    if ptn == rb"]\s+#":
                        # Look for complete prompt pattern [DeviceName] #
                        prompt_matches = re.findall(rb"\[.+?\]\s+#", buffer)
                        if prompt_matches:
                            # Find last occurrence (most recent prompt)
                            last_prompt = prompt_matches[-1]
                            prompt_index = buffer.rfind(last_prompt) + len(last_prompt)
                            result = buffer[:prompt_index]
                            # Save remaining data for next read
                            self._receive_buffer = buffer[prompt_index:]
                            return result
                    else:
                        pattern_index = buffer.find(ptn) + len(ptn)
                        result = buffer[:pattern_index]
                        # Save remaining data for next read
                        self._receive_buffer = buffer[pattern_index:]
                        return result

            # Check for complete command prompt using regex
            if b"#" in buffer:
                # Convert buffer to string for line-by-line checking
                buffer_str = buffer.decode("utf-8", errors="ignore")
                lines = buffer_str.splitlines()

                # Check if any line is a command prompt
                for i, line in enumerate(lines):
                    if is_command_prompt(line):
                        # Calculate how many bytes to include
                        included_lines = "\n".join(lines[: i + 1])
                        bytes_to_include = len(
                            included_lines.encode("utf-8", errors="ignore")
                        )

                        result = buffer[:bytes_to_include]
                        # Save remaining data for next read
                        self._receive_buffer = buffer[bytes_to_include:]
                        return result

            # Check for data timeout - if we haven't received data in a while
            # but the overall timeout hasn't been reached yet
            data_timeout = time.time() - last_data_time > 2.0

            # Break if we've made too many attempts or hit a data timeout
            if attempt_count >= max_attempts or data_timeout:
                _LOGGER.debug(
                    "Breaking read loop after %d attempts or data timeout %s",
                    attempt_count,
                    data_timeout,
                )
                break

            # Calculate remaining time
            remaining_time = timeout - (time.time() - start_time)
            if remaining_time <= 0:
                break

            # Read more data with shorter timeouts for responsiveness
            read_timeout = min(0.5, remaining_time)

            try:
                data = await self._socket_read(timeout=read_timeout)
                attempt_count += 1

                if not data:
                    # No data received in this read attempt
                    if time.time() - start_time >= timeout:
                        # Overall timeout reached
                        _LOGGER.debug("Timeout reached while waiting for pattern")
                        break

                    # Brief pause before next attempt to avoid CPU spinning
                    await asyncio.sleep(0.1)
                    continue

                # Got new data - update last received time
                last_data_time = time.time()

                # Append new data to buffer
                buffer += data
                _LOGGER.debug(
                    "Read %d bytes from socket, buffer now %d bytes, looking for %s",
                    len(data),
                    len(buffer),
                    pattern,
                )

                # If buffer size hasn't changed significantly after multiple attempts,
                # we might be stuck in a loop without receiving the prompt
                if len(buffer) - last_buffer_size < 2 and attempt_count > 5:
                    _LOGGER.debug("Buffer size not increasing, may be missing prompt")
                    # Look for potential command prompts
                    if b"#" in buffer or b">" in buffer:
                        _LOGGER.debug("Found potential prompt character in buffer")
                        # Just before timeout, check if buffer looks complete
                        buffer_str = buffer.decode("utf-8", errors="ignore")
                        if re.search(r"\[[^\]]+\]\s+#", buffer_str):
                            _LOGGER.debug(
                                "Found command prompt pattern, returning data"
                            )
                            # Return the buffer and let the caller parse it
                            result = buffer
                            self._receive_buffer = b""
                            return result

                    # Force return what we have if content seems substantial
                    if len(buffer) > 20:
                        break

                last_buffer_size = len(buffer)

                # Debug output for raw data to help debug connection issues
                if len(buffer) < 200:  # Only log small buffers to avoid flooding
                    _LOGGER.debug("Buffer content: %s", buffer)

            except Exception as e:
                _LOGGER.error("Error reading until pattern: %s", e)
                self._connected = False
                return b""

        # Timeout reached, log and return partial data
        _LOGGER.debug(
            "Read until timeout (%s seconds) - returning %d bytes of partial data",
            timeout,
            len(buffer),
        )

        # Save the buffer for future reads
        self._receive_buffer = buffer
        return buffer

    async def cycle_outlet(self, outlet_num: int) -> bool:
        """Cycle power for a specific outlet."""
        _LOGGER.debug("Cycling outlet %s", outlet_num)

        try:
            # Direct cycle command
            cmd = f"power outlets cycle {outlet_num}"
            response = await self.send_command(cmd)

            # Check if command was successful
            if "unknown command" in response.lower() or "error" in response.lower():
                _LOGGER.warning("Cycle command not recognized: %s", response)
                # Fall back to manual on/off cycling
                _LOGGER.debug("Trying manual cycle (off then on)")

                # Turn off first
                off_success = await self.direct_turn_outlet_off(outlet_num)
                if not off_success:
                    _LOGGER.error("Failed to turn outlet %s off for cycle", outlet_num)
                    return False

                # Wait for cycle delay
                await asyncio.sleep(5)

                # Turn back on
                on_success = await self.direct_turn_outlet_on(outlet_num)
                if not on_success:
                    _LOGGER.error(
                        "Failed to turn outlet %s back on for cycle", outlet_num
                    )
                    return False

                _LOGGER.info("Successfully manually cycled outlet %s", outlet_num)
                return True

            _LOGGER.info("Successfully cycled outlet %s", outlet_num)
            return True

        except Exception as err:
            _LOGGER.error("Error cycling outlet %s: %s", outlet_num, err)
            return False

    async def cycle_all_outlets(self) -> bool:
        """Cycle power for all outlets."""
        if not self._connected:
            _LOGGER.warning("Not connected, cannot cycle all outlets")
            return False

        try:
            capabilities = self.get_model_capabilities()
            outlet_count = capabilities.get("num_outlets", 8)

            _LOGGER.info("Cycling power for all %d outlets", outlet_count)

            # Turn all outlets off
            off_command = "power outlets all off /y"
            await self.send_command(off_command)

            # Wait 5 seconds
            await asyncio.sleep(5)

            # Turn all outlets back on
            on_command = "power outlets all on /y"
            await self.send_command(on_command)

            # Update internal states
            for i in range(1, outlet_count + 1):
                self._outlet_states[i] = True

            return True
        except Exception as e:
            _LOGGER.error("Error cycling all outlets: %s", e)
            return False

    async def turn_outlet_on(self, outlet_num: int) -> bool:
        """Turn a specific outlet on."""
        if not self._connected:
            _LOGGER.warning("Not connected, cannot turn on outlet %d", outlet_num)
            await self._handle_connection_issues()
            if not self._connected:
                return False

        try:
            _LOGGER.info("Turning on outlet %d", outlet_num)
            command = f"power outlets {outlet_num} on /y"
            response = await self.send_command(command, timeout=10, wait_time=1.0)

            # Check if the command was recognized
            if (
                not response
                or "unrecognized" in response.lower()
                or "invalid" in response.lower()
            ):
                _LOGGER.warning(
                    "Standard outlet ON command not recognized, trying alternative method"
                )
                return await self.direct_turn_outlet_on(outlet_num)

            if response:
                # Update our internal state
                self._outlet_states[outlet_num] = True

                # Verify the state change by getting updated details
                try:
                    # Only try to verify if we're still connected
                    if self._connected:
                        state = await self.get_outlet_state(outlet_num)
                        if not state:
                            _LOGGER.warning(
                                "Outlet %d may not have turned ON properly", outlet_num
                            )
                    else:
                        _LOGGER.warning("Not connected, cannot verify outlet state")
                except Exception as e:
                    _LOGGER.warning("Error verifying outlet state: %s", e)

                # Consider the command successful even if verification failed
                return True
            else:
                _LOGGER.error("No response when turning on outlet %d", outlet_num)
                return await self.direct_turn_outlet_on(outlet_num)

        except Exception as e:
            _LOGGER.error("Error turning on outlet %d: %s", outlet_num, e)
            return await self.direct_turn_outlet_on(outlet_num)

    async def turn_outlet_off(self, outlet_num: int) -> bool:
        """Turn a specific outlet off."""
        if not self._connected:
            _LOGGER.warning("Not connected, cannot turn off outlet %d", outlet_num)
            await self._handle_connection_issues()
            if not self._connected:
                return False

        try:
            _LOGGER.info("Turning off outlet %d", outlet_num)
            command = f"power outlets {outlet_num} off /y"
            response = await self.send_command(command, timeout=10, wait_time=1.0)

            # Check if the command was recognized
            if (
                not response
                or "unrecognized" in response.lower()
                or "invalid" in response.lower()
            ):
                _LOGGER.warning(
                    "Standard outlet OFF command not recognized, trying alternative method"
                )
                return await self.direct_turn_outlet_off(outlet_num)

            if response:
                # Update our internal state
                self._outlet_states[outlet_num] = False

                # Verify the state change by getting updated details
                try:
                    # Only try to verify if we're still connected
                    if self._connected:
                        state = await self.get_outlet_state(outlet_num)
                        if state:
                            _LOGGER.warning(
                                "Outlet %d may not have turned OFF properly", outlet_num
                            )
                    else:
                        _LOGGER.warning("Not connected, cannot verify outlet state")
                except Exception as e:
                    _LOGGER.warning("Error verifying outlet state: %s", e)

                # Consider the command successful even if verification failed
                return True
            else:
                _LOGGER.error("No response when turning off outlet %d", outlet_num)
                return await self.direct_turn_outlet_off(outlet_num)

        except Exception as e:
            _LOGGER.error("Error turning off outlet %d: %s", outlet_num, e)
            return await self.direct_turn_outlet_off(outlet_num)

    async def get_outlet_state(self, outlet_num: int) -> bool:
        """Get the current state of a specific outlet."""
        if not self._connected:
            _LOGGER.debug("Not connected, cannot get outlet state")
            return False

        try:
            _LOGGER.info("Getting state for outlet %d", outlet_num)
            command = f"show outlets {outlet_num} details"
            _LOGGER.debug("Sending command to get outlet state: %s", command)
            response = await self.send_command(command)

            if not response:
                _LOGGER.error(
                    "No response getting outlet state for outlet %d", outlet_num
                )
                return await self.get_direct_outlet_state(outlet_num)

            # Check if we got an unrecognized argument error
            if "unrecognized" in response.lower() or "invalid" in response.lower():
                _LOGGER.warning("Command not recognized, trying alternative approach")
                return await self.get_direct_outlet_state(outlet_num)

            # Log response length for debugging
            _LOGGER.debug(
                "Received response of length %d for outlet %d",
                len(response),
                outlet_num,
            )

            # Import parser here to avoid circular imports
            from .parser import parse_outlet_state

            # Use the robust parser function
            state = parse_outlet_state(response, outlet_num)

            if state is not None:
                # Update our internal state
                self._outlet_states[outlet_num] = state
                _LOGGER.info(
                    "Outlet %d state determined successfully: %s",
                    outlet_num,
                    "ON" if state else "OFF",
                )
                return state
            else:
                _LOGGER.error(
                    "Could not parse outlet state from response for outlet %d - trying direct method",
                    outlet_num,
                )
                # Try alternative approach
                return await self.get_direct_outlet_state(outlet_num)

        except Exception as e:
            _LOGGER.error("Error getting outlet state for outlet %d: %s", outlet_num, e)
            return self._outlet_states.get(outlet_num, False)

    async def get_all_outlet_states(self, force_refresh: bool = False) -> dict:
        """Get states of all outlets."""
        if not self._connected:
            _LOGGER.warning("Not connected, cannot get outlet states")
            return self._outlet_states

        if not force_refresh and self._outlet_states:
            # Return cached states if available
            return self._outlet_states

        _LOGGER.debug("Getting all outlet states")

        try:
            # Use the power outlets status command to get states of all outlets at once
            cmd = "power outlets status"
            response = await self.send_command(cmd)

            if not response or "Unknown command" in response:
                _LOGGER.warning(
                    "Standard '%s' command not recognized, trying alternatives", cmd
                )
                # Try fallback approach - get states individually
                await self._get_outlet_states_individually()
                return self._outlet_states

            # Find all "Outlet X is On/Off" patterns in the response
            outlet_states = {}
            matches = re.findall(
                r"Outlet\s+(\d+)\s+is\s+(On|Off)", response, re.IGNORECASE
            )

            if matches:
                for match in matches:
                    try:
                        outlet_num = int(match[0])
                        state = match[1].lower() == "on"
                        outlet_states[outlet_num] = state
                        _LOGGER.debug(
                            "Found outlet %d state: %s",
                            outlet_num,
                            "ON" if state else "OFF",
                        )
                    except (ValueError, IndexError) as e:
                        _LOGGER.error("Error parsing outlet state: %s - %s", match, e)

                # Update internal state
                if outlet_states:
                    self._outlet_states.update(outlet_states)
                    _LOGGER.debug("Updated states for %d outlets", len(outlet_states))
                    return self._outlet_states

            # If we reach here, no states were found in the response
            _LOGGER.warning(
                "No outlet states found in response, falling back to individual queries"
            )
            await self._get_outlet_states_individually()

        except Exception as e:
            _LOGGER.error("Error getting all outlet states: %s", e)

        return self._outlet_states

    async def _get_outlet_states_individually(self):
        """Get outlet states one by one as a fallback method."""
        _LOGGER.debug("Getting outlet states individually")

        capabilities = self.get_model_capabilities()
        num_outlets = capabilities.get("num_outlets", 8)

        for outlet_num in range(1, num_outlets + 1):
            try:
                # Get state of each outlet
                cmd = f"power outlets status {outlet_num}"
                response = await self.send_command(cmd)

                if response:
                    # Use the parser to extract the state
                    from .parser import parse_outlet_state

                    state = parse_outlet_state(response, outlet_num)

                    if state is not None:
                        self._outlet_states[outlet_num] = state
                        _LOGGER.debug(
                            "Outlet %d state: %s", outlet_num, "ON" if state else "OFF"
                        )
                    else:
                        # Assume OFF if state couldn't be determined
                        self._outlet_states[outlet_num] = False
                        _LOGGER.warning(
                            "Could not determine state for outlet %d, assuming OFF",
                            outlet_num,
                        )
                else:
                    # Assume OFF if no response
                    self._outlet_states[outlet_num] = False
                    _LOGGER.warning(
                        "No response for outlet %d status, assuming OFF", outlet_num
                    )

            except Exception as e:
                _LOGGER.error("Error getting state for outlet %d: %s", outlet_num, e)
                # Assume OFF in case of error
                self._outlet_states[outlet_num] = False

    async def set_outlet_name(self, outlet_num: int, name: str) -> bool:
        """Set the name of a specific outlet."""
        if not self._connected:
            _LOGGER.warning("Not connected, cannot set outlet name")
            await self.reconnect()
            if not self._connected:
                return False

        try:
            # First enter config mode
            _LOGGER.debug("Entering config mode to set outlet name")
            config_response = await self.send_command("config")

            if "config" not in config_response.lower():
                _LOGGER.error("Failed to enter config mode")
                return False

            # Set the outlet name
            _LOGGER.info("Setting outlet %d name to '%s'", outlet_num, name)
            # Properly escape quotes in the name
            sanitized_name = name.replace('"', '\\"')
            name_cmd = f'outlet {outlet_num} name "{sanitized_name}"'
            name_response = await self.send_command(name_cmd)

            # Apply changes
            apply_response = await self.send_command("apply")

            # Check if apply was successful
            if "applied" in apply_response.lower() or "#" in apply_response:
                # Update our cached data
                self._outlet_names[outlet_num] = name
                _LOGGER.debug(
                    "Successfully set outlet %d name to '%s'", outlet_num, name
                )
                return True
            else:
                _LOGGER.error("Failed to apply outlet name change")
                return False

        except Exception as e:
            _LOGGER.error("Error setting outlet name: %s", e)
            return False

    async def set_pdu_name(self, name: str) -> bool:
        """Set the name of the PDU."""
        if not self._connected:
            _LOGGER.warning("Not connected, cannot set PDU name")
            await self.reconnect()
            if not self._connected:
                return False

        try:
            # First enter config mode
            _LOGGER.debug("Entering config mode to set PDU name")
            config_response = await self.send_command("config")

            if "config" not in config_response.lower():
                _LOGGER.error("Failed to enter config mode")
                return False

            # Set the PDU name
            _LOGGER.info("Setting PDU name to '%s'", name)
            # Properly escape quotes in the name
            sanitized_name = name.replace('"', '\\"')
            name_cmd = f'pdu name "{sanitized_name}"'
            name_response = await self.send_command(name_cmd)

            # Apply changes
            apply_response = await self.send_command("apply")

            # Check if apply was successful
            if "applied" in apply_response.lower() or "#" in apply_response:
                # Update our cached data
                self._pdu_name = name
                self._pdu_info["name"] = name
                _LOGGER.debug("Successfully set PDU name to '%s'", name)
                return True
            else:
                _LOGGER.error("Failed to apply PDU name change")
                return False

        except Exception as e:
            _LOGGER.error("Error setting PDU name: %s", e)
            return False

    async def queue_command(self, command: str) -> str:
        """Queue a command to be executed by the command processor.

        This helps prevent overwhelming the device with too many commands at once.
        """
        if not self._connected:
            _LOGGER.warning("Not connected, cannot queue command: %s", command)
            return ""

        # Create a future to receive the result
        future = asyncio.get_running_loop().create_future()

        # Add command and future to the queue
        await self._command_queue.put((command, future))

        try:
            # Wait for the result with a timeout
            return await asyncio.wait_for(future, timeout=self._command_timeout * 2)
        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout waiting for queued command result: %s", command)
            return ""
        except Exception as e:
            _LOGGER.error(
                "Error waiting for queued command result: %s - %s", command, e
            )
            return ""

    async def get_sensor_values(self, force_refresh: bool = False) -> dict:
        """Get all sensor values from the PDU."""
        if not self._connected and not force_refresh:
            _LOGGER.debug("Not connected, cannot get sensor values")
            return self._sensors

        try:
            # Import parser here to avoid circular imports
            from .parser import parse_pdu_power_data, parse_pdu_temperature

            # Get the capabilities for this model
            capabilities = self.get_model_capabilities()

            # If this model supports power monitoring, get the power data
            if capabilities.get("supports_power_monitoring", False):
                try:
                    # Get PDU power data
                    pdu_cmd = "show pdu power"
                    power_response = await self.send_command(pdu_cmd)

                    if power_response:
                        # Parse power data
                        power_data = parse_pdu_power_data(power_response)
                        if power_data:
                            # Update sensors with parsed data
                            self._sensors.update(power_data)
                except Exception as e:
                    _LOGGER.error("Error getting PDU power data: %s", e)

            # If this model has a temperature sensor, get the temperature
            if capabilities.get("has_temperature_sensor", False):
                try:
                    # Get temperature data
                    temp_cmd = "show pdu temperature"
                    temp_response = await self.send_command(temp_cmd)

                    if temp_response:
                        # Parse temperature data
                        temp_data = parse_pdu_temperature(temp_response)
                        if temp_data:
                            # Update sensors with parsed data
                            self._sensors.update(temp_data)
                except Exception as e:
                    _LOGGER.error("Error getting PDU temperature data: %s", e)

            return self._sensors

        except Exception as e:
            _LOGGER.error("Error getting sensor values: %s", e)
            return self._sensors

    async def discover_valid_commands(self) -> dict:
        """Discover what commands are valid for this device."""
        _LOGGER.info("Starting command discovery to learn device syntax")

        command_map = {}

        # Use shorter timeouts for command discovery
        discovery_timeout = 2.0

        # First, try essential power and show commands with shorter timeouts
        essential_commands = [
            "power",  # Basic power command
            "power status",  # Power status
            "power outlets 1",  # Test with a specific outlet
            "show",  # Basic show command
            "help power",  # Help for power commands
            "config",  # Config mode
        ]

        for cmd in essential_commands:
            try:
                _LOGGER.debug("Testing essential command: %s", cmd)
                response = await self.send_command(
                    cmd, timeout=discovery_timeout, retry_alternative_command=False
                )

                command_accepted = (
                    response
                    and "unknown command" not in response.lower()
                    and "unrecognized" not in response.lower()
                )

                if command_accepted:
                    _LOGGER.info("Essential command '%s' is valid", cmd)
                    command_map[cmd] = True

                    # Fast path: If power commands are already working, we can exit early with minimal verification
                    if cmd in ["power", "power status"]:
                        # Find what power syntax this device supports
                        if (
                            "power outlets" in response.lower()
                            or "outlet" in response.lower()
                        ):
                            _LOGGER.info("Found power outlet command support")
                            command_map["power_outlets_supported"] = True
                        if "on" in response.lower() and "off" in response.lower():
                            _LOGGER.info("Found on/off control support")
                            command_map["power_control_supported"] = True
                else:
                    command_map[cmd] = False
                    _LOGGER.debug("Command '%s' is not recognized", cmd)
            except Exception as e:
                _LOGGER.error("Error testing command '%s': %s", cmd, str(e))
                command_map[cmd] = False

        # If we found enough info, return early to avoid timeouts
        if command_map.get("power", False) and (
            command_map.get("power_outlets_supported", False)
            or command_map.get("power_control_supported", False)
        ):
            _LOGGER.info("Found sufficient command support, using simplified discovery")
            return command_map

        # If we haven't found working commands yet, expand the search
        secondary_commands = [
            "power outlets",  # Power outlets command
            "power outlet",  # Try singular
            "show power",  # Show power info
            "show outlets",  # Show outlets status
            "show outlet",  # Try singular
        ]

        for cmd in secondary_commands:
            if cmd not in command_map:
                try:
                    _LOGGER.debug("Testing secondary command: %s", cmd)
                    response = await self.send_command(
                        cmd, timeout=discovery_timeout, retry_alternative_command=False
                    )

                    command_accepted = (
                        response
                        and "unknown command" not in response.lower()
                        and "unrecognized" not in response.lower()
                    )

                    if command_accepted:
                        _LOGGER.info("Secondary command '%s' is valid", cmd)
                        command_map[cmd] = True
                    else:
                        command_map[cmd] = False
                        _LOGGER.debug("Command '%s' is not recognized", cmd)
                except Exception as e:
                    _LOGGER.error("Error testing command '%s': %s", cmd, str(e))
                    command_map[cmd] = False

        # Log discovery results
        _LOGGER.info(
            "Command discovery complete, found %d valid commands",
            len(
                [
                    k
                    for k, v in command_map.items()
                    if v and isinstance(k, str) and not k.startswith("_")
                ]
            ),
        )
        self._valid_commands = command_map
        return command_map

    async def get_direct_outlet_state(self, outlet_num: int) -> bool:
        """Try alternative methods to get outlet state."""
        if not self._connected:
            _LOGGER.debug("Not connected, cannot get outlet state")
            return False

        # Use shorter timeout for state queries
        state_timeout = 2.0

        # First check if we already have a cached state
        if outlet_num in self._outlet_states:
            _LOGGER.debug("Using cached state for outlet %d", outlet_num)
            return self._outlet_states[outlet_num]

        # Try power-specific commands based on the device's help output
        commands = [
            f"power outlet {outlet_num} status",  # Try explicit status check
            f"power outlet {outlet_num}",  # Try just the outlet number
            "power status",  # Try general power status
        ]

        # Check if we have discovered valid commands
        if hasattr(self, "_valid_commands") and self._valid_commands:
            # Prioritize commands we know work
            valid_cmds = [
                cmd
                for cmd in commands
                if any(
                    vcmd in cmd
                    for vcmd, is_valid in self._valid_commands.items()
                    if is_valid and isinstance(vcmd, str)
                )
            ]
            if valid_cmds:
                _LOGGER.debug(
                    "Using %d validated commands from discovery", len(valid_cmds)
                )
                commands = valid_cmds

        for cmd in commands:
            _LOGGER.debug("Trying command for outlet state: %s", cmd)
            try:
                response = await self.send_command(
                    cmd, timeout=state_timeout, retry_alternative_command=False
                )

                if not response:
                    continue

                # Check for "unknown command" errors
                if (
                    "unknown command" in response.lower()
                    or "unrecognized" in response.lower()
                ):
                    _LOGGER.debug("Command '%s' not recognized", cmd)
                    continue

                # If we have a response, look for state indicators
                _LOGGER.debug("Got response from '%s' (%d bytes)", cmd, len(response))

                # Look for state indicators in response
                on_indicators = ["on", "active", "enabled", "power on"]
                off_indicators = ["off", "inactive", "disabled", "power off"]

                response_lower = response.lower()

                # Check for on indicators
                for indicator in on_indicators:
                    if indicator in response_lower:
                        _LOGGER.info(
                            "Found ON indicator '%s' for outlet %d",
                            indicator,
                            outlet_num,
                        )
                        self._outlet_states[outlet_num] = True
                        return True

                # Check for off indicators
                for indicator in off_indicators:
                    if indicator in response_lower:
                        _LOGGER.info(
                            "Found OFF indicator '%s' for outlet %d",
                            indicator,
                            outlet_num,
                        )
                        self._outlet_states[outlet_num] = False
                        return False

                # If we got a response but couldn't determine the state,
                # look for outlet-specific information
                outlet_pattern = rf"outlet\s+{outlet_num}.*?(\w+)"
                matches = re.finditer(outlet_pattern, response_lower)
                for match in matches:
                    try:
                        state_word = match.group(1).lower()
                        if state_word in ["on", "active", "enabled"]:
                            _LOGGER.info(
                                "Found outlet %d in ON state via pattern match",
                                outlet_num,
                            )
                            self._outlet_states[outlet_num] = True
                            return True
                        elif state_word in ["off", "inactive", "disabled"]:
                            _LOGGER.info(
                                "Found outlet %d in OFF state via pattern match",
                                outlet_num,
                            )
                            self._outlet_states[outlet_num] = False
                            return False
                    except Exception as e:
                        _LOGGER.debug("Error parsing outlet pattern match: %s", e)
            except Exception as e:
                _LOGGER.warning("Error with command '%s': %s", cmd, e)

        # If we've reached this point, we couldn't determine the state with primary commands
        # Skip config mode and help probing to avoid timeouts

        # If we still couldn't determine state, assume outlet is OFF
        _LOGGER.warning(
            "Could not determine state for outlet %d, assuming OFF", outlet_num
        )
        self._outlet_states[outlet_num] = False
        return False

    async def direct_turn_outlet_on(self, outlet_num: int) -> bool:
        """Turn an outlet on using a direct command (without verification).

        This is a more direct approach than turn_outlet_on which includes verification.
        """
        _LOGGER.debug("Directly turning outlet %s ON", outlet_num)

        try:
            # Send direct power command to turn on outlet
            cmd = f"power outlets on {outlet_num}"
            response = await self.send_command(cmd)

            # Check for error responses
            if "error" in response.lower() or "unknown command" in response.lower():
                _LOGGER.warning("Error turning outlet %s ON: %s", outlet_num, response)
                return False

            # Update internal state
            self._outlet_states[outlet_num] = True
            return True
        except Exception as err:
            _LOGGER.error("Exception turning outlet %s ON: %s", outlet_num, err)
            return False

    async def direct_turn_outlet_off(self, outlet_num: int) -> bool:
        """Turn an outlet off using a direct command (without verification).

        This is a more direct approach than turn_outlet_off which includes verification.
        """
        _LOGGER.debug("Directly turning outlet %s OFF", outlet_num)

        try:
            # Send direct power command to turn off outlet
            cmd = f"power outlets off {outlet_num}"
            response = await self.send_command(cmd)

            # Check for error responses
            if "error" in response.lower() or "unknown command" in response.lower():
                _LOGGER.warning("Error turning outlet %s OFF: %s", outlet_num, response)
                return False

            # Update internal state
            self._outlet_states[outlet_num] = False
            return True
        except Exception as err:
            _LOGGER.error("Exception turning outlet %s OFF: %s", outlet_num, err)
            return False
