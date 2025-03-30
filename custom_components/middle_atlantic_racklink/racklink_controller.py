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
    # RackLink Power Management
    "RLNK-415": {"num_outlets": 4, "has_current_sensing": False},
    "RLNK-415R": {"num_outlets": 4, "has_current_sensing": False},
    "RLNK-520": {"num_outlets": 5, "has_current_sensing": False},
    "RLNK-520L": {"num_outlets": 5, "has_current_sensing": False},
    "RLNK-615": {"num_outlets": 6, "has_current_sensing": False},
    "RLNK-615L": {"num_outlets": 6, "has_current_sensing": False},
    "RLNK-920": {"num_outlets": 9, "has_current_sensing": False},
    "RLNK-920L": {"num_outlets": 9, "has_current_sensing": False},
    "RLNK-215": {"num_outlets": 2, "has_current_sensing": False},
    "RLNK-P915": {"num_outlets": 9, "has_current_sensing": False},
    "RLNK-P920": {"num_outlets": 9, "has_current_sensing": False},
    # RackLink Select Power Management
    "RLNK-SL415": {"num_outlets": 4, "has_current_sensing": False},
    "RLNK-SL520": {"num_outlets": 5, "has_current_sensing": False},
    "RLNK-SL615": {"num_outlets": 6, "has_current_sensing": False},
    "RLNK-SL920": {"num_outlets": 9, "has_current_sensing": False},
    # RackLink Metered Power Management (with current sensing)
    "RLM-15": {"num_outlets": 8, "has_current_sensing": True},
    "RLM-15A": {"num_outlets": 8, "has_current_sensing": True},
    "RLM-20": {"num_outlets": 8, "has_current_sensing": True},
    "RLM-20A": {"num_outlets": 8, "has_current_sensing": True},
    "RLM-20-1": {"num_outlets": 8, "has_current_sensing": True},
    "RLM-20L": {"num_outlets": 8, "has_current_sensing": True},
    # Default if model not specified
    "DEFAULT": {"num_outlets": 8, "has_current_sensing": False},
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
        """Initialize the controller."""
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._scan_interval = scan_interval
        self._socket_timeout = socket_timeout
        self._login_timeout = login_timeout
        self._command_timeout = command_timeout

        self._model = None
        self._serial_number = None
        self._firmware_version = None
        self._pdu_name = None
        self._mac_address = None
        self._outlet_names = {}
        self._outlet_states = {}

        # Initialize all sensor data dictionaries
        self._outlet_power_data = {}
        self._outlet_power = {}
        self._outlet_current = {}
        self._outlet_energy = {}
        self._outlet_voltage = {}
        self._outlet_power_factor = {}
        self._sensor_values = {}
        self._pdu_info = {}  # Initialize PDU info dictionary
        self._outlet_non_critical = {}  # Initialize outlet non-critical flag dictionary
        self._sensors = {}  # Initialize sensors dictionary for PDU-level sensors

        self._last_update = None
        self._last_error = None
        self._last_error_time = None

        self._socket = None
        self._connected = False
        self._available = False
        self._connection_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()
        self._buffer = b""
        self._command_queue = asyncio.Queue()
        self._command_processor = None
        self._retry_count = 0
        self._max_retries = 3
        self._command_delay = (
            0.25  # Delay between commands to avoid overwhelming device
        )
        self._connection_task = None
        self._shutdown_requested = False

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
        return self._serial_number

    @property
    def pdu_name(self) -> Optional[str]:
        """Return the PDU name."""
        return self._pdu_name

    @property
    def pdu_model(self) -> Optional[str]:
        """Return the PDU model."""
        return self._model

    @property
    def pdu_firmware(self) -> Optional[str]:
        """Return the PDU firmware version."""
        return self._firmware_version

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
        if not self._pdu_info and (self._model or self._serial_number):
            self._pdu_info = {
                "model": self._model,
                "firmware": self._firmware_version,
                "serial": self._serial_number,
                "name": self._pdu_name,
                "mac_address": self._mac_address,
            }
        return self._pdu_info

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
        """Connect to the device and authenticate."""
        async with self._connection_lock:
            if self._connected and self._socket:
                # Verify socket is still valid
                try:
                    if self._socket.fileno() == -1:
                        _LOGGER.debug("Socket appears to be closed, reconnecting")
                        self._connected = False
                        self._socket = None
                    else:
                        _LOGGER.debug("Already connected to %s", self._host)
                        return True
                except (OSError, ValueError):
                    _LOGGER.debug("Socket error, needs reconnection")
                    self._connected = False
                    self._socket = None

            _LOGGER.info("Connecting to %s:%s", self._host, self._port)

            # Reset the retry counter when we start a new connection attempt
            self._retry_count = 0

            try:
                # Close any existing socket first
                await self._close_socket()

                # Create socket with a reasonable timeout
                self._socket = await asyncio.wait_for(
                    self._create_socket_connection(), timeout=self._socket_timeout
                )

                if not self._socket:
                    _LOGGER.error(
                        "Failed to create socket connection to %s:%s",
                        self._host,
                        self._port,
                    )
                    return False

                # Try to log in with a timeout
                login_success = await asyncio.wait_for(
                    self._login(), timeout=self._login_timeout
                )

                if login_success:
                    self._connected = True
                    self._available = True
                    _LOGGER.info(
                        "Successfully connected to %s:%s", self._host, self._port
                    )

                    # Ensure command processor is running
                    self._ensure_command_processor_running()

                    # Get device info
                    try:
                        await asyncio.wait_for(
                            self.get_device_info(), timeout=self._command_timeout * 2
                        )
                    except asyncio.TimeoutError:
                        _LOGGER.warning(
                            "Timeout while getting device info - continuing anyway"
                        )
                    except Exception as e:
                        _LOGGER.warning(
                            "Error getting device info: %s - continuing anyway", e
                        )

                    return True
                else:
                    _LOGGER.error("Failed to log in to %s:%s", self._host, self._port)
                    await self._close_socket()
                    return False

            except asyncio.TimeoutError:
                _LOGGER.error(
                    "Connection attempt to %s:%s timed out", self._host, self._port
                )
                await self._close_socket()
                return False
            except Exception as e:
                _LOGGER.error(
                    "Error during connection to %s:%s: %s", self._host, self._port, e
                )
                await self._close_socket()
                return False

    async def _load_initial_data(self):
        """Load initial data from the PDU."""
        _LOGGER.debug("Loading initial PDU data")
        try:
            # Get PDU model information
            await self.get_device_info()

            # Initialize outlet states
            await self.get_all_outlet_states()
        except Exception as e:
            _LOGGER.error("Error loading initial PDU data: %s", e)

    def _normalize_model_name(self, model_string: str) -> str:
        """
        DEPRECATED: This method is now provided by the parser module.
        Use parser.normalize_model_name() instead.

        This method is kept for backward compatibility but will be removed in a future version.
        """
        from .parser import normalize_model_name

        return normalize_model_name(model_string)

    async def get_device_info(self) -> dict:
        """Get device information."""
        _LOGGER.debug("Getting device information for %s", self._host)

        if not self._connected:
            _LOGGER.debug("Not connected during get_device_info, attempting to connect")
            if not await self.reconnect():
                _LOGGER.warning("Could not connect to get device info")
                return {}

        # Only fetch device info if we don't have it yet
        if not self._model or not self._serial_number:
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
                        self._model = normalize_model_name(raw_model)
                        _LOGGER.debug(
                            "Found PDU model: %s (normalized from %s)",
                            self._model,
                            raw_model,
                        )

                    if "serial" in device_info:
                        self._serial_number = device_info["serial"]
                        _LOGGER.debug("Found PDU serial: %s", self._serial_number)

                    if "firmware" in device_info:
                        self._firmware_version = device_info["firmware"]
                        _LOGGER.debug("Found PDU firmware: %s", self._firmware_version)

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
        if not self._model:
            _LOGGER.warning("Could not determine PDU model, using default")
            self._model = "RLNK-P920R"  # Default model

        if not self._pdu_name:
            self._pdu_name = f"RackLink PDU {self._host}"

        if not self._serial_number:
            # Generate a pseudo-serial based on MAC if we have it, otherwise use host
            if self._mac_address:
                self._serial_number = f"UNKNOWN-{self._mac_address.replace(':', '')}"
            else:
                self._serial_number = f"UNKNOWN-{self._host.replace('.', '')}"

        # Update PDU info dictionary
        self._pdu_info = {
            "model": self._model or "Unknown",
            "firmware": self._firmware_version or "Unknown",
            "serial": self._serial_number or "Unknown",
            "name": self._pdu_name or "RackLink PDU",
            "mac_address": self._mac_address or "Unknown",
        }

        _LOGGER.debug("Device info: %s", self._pdu_info)
        return self._pdu_info

    def get_model_capabilities(self) -> Dict[str, Any]:
        """Get the capabilities for the current model."""
        model = self._model or "DEFAULT"
        # Use the model-specific capabilities or fallback to default
        base_capabilities = MODEL_CAPABILITIES.get(model, None)

        # Try variations of the model name if exact match not found
        if base_capabilities is None:
            # Try without the "R" suffix (RLNK-P920R -> RLNK-P920)
            if model.endswith("R"):
                base_capabilities = MODEL_CAPABILITIES.get(model[:-1], None)

            # Try without the "SP" suffix (RLNK-P920R-SP -> RLNK-P920R)
            if model.endswith("-SP") and base_capabilities is None:
                base_capabilities = MODEL_CAPABILITIES.get(model[:-3], None)

            # Try with just the base model family
            if base_capabilities is None:
                for pattern in ["P9", "P4", "RLM"]:
                    if pattern in model:
                        # Find the closest match in MODEL_CAPABILITIES
                        for cap_model in MODEL_CAPABILITIES:
                            if pattern in cap_model:
                                base_capabilities = MODEL_CAPABILITIES[cap_model]
                                _LOGGER.debug(
                                    "Using capabilities from similar model %s for %s",
                                    cap_model,
                                    model,
                                )
                                break
                        if base_capabilities:
                            break

            # If still no match, use default
            if base_capabilities is None:
                _LOGGER.warning(
                    "No capabilities found for model %s, using default", model
                )
                base_capabilities = MODEL_CAPABILITIES["DEFAULT"]

        # Start with base capabilities for this model
        capabilities = base_capabilities.copy()

        # For P series (newer models) we can infer some capabilities
        if "P9" in model or "P4" in model:
            capabilities.update(
                {
                    "supports_power_monitoring": True,
                    "supports_outlet_switching": True,
                    "supports_energy_monitoring": True,
                    "supports_outlet_scheduling": False,
                    "max_current": (
                        20 if "20" in model else 15
                    ),  # Extract from model number
                    "has_surge_protection": "-SP" in model,
                    "has_temperature_sensor": True,  # Most Pro models have temperature sensing
                }
            )

        # For RLNK-P920R model specifically
        if model in ["RLNK-P920R", "RLNK-P920", "RLNK-P920R-SP"]:
            capabilities.update(
                {
                    "num_outlets": 9,
                    "supports_power_monitoring": True,
                    "supports_outlet_switching": True,
                    "supports_energy_monitoring": True,
                    "supports_outlet_scheduling": False,
                    "max_current": 20,
                    "has_surge_protection": "-SP" in model,
                    "has_temperature_sensor": True,
                }
            )
        elif model in ["RLNK-P915R", "RLNK-P915", "RLNK-P915R-SP"]:
            capabilities.update(
                {
                    "num_outlets": 9,
                    "supports_power_monitoring": True,
                    "supports_outlet_switching": True,
                    "supports_energy_monitoring": True,
                    "supports_outlet_scheduling": False,
                    "max_current": 15,
                    "has_surge_protection": "-SP" in model,
                    "has_temperature_sensor": True,
                }
            )
        elif model in ["RLNK-P415R", "RLNK-P415", "RLNK-P415R-SP"]:
            capabilities.update(
                {
                    "num_outlets": 4,
                    "supports_power_monitoring": True,
                    "supports_outlet_switching": True,
                    "supports_energy_monitoring": True,
                    "supports_outlet_scheduling": False,
                    "max_current": 15,
                    "has_surge_protection": "-SP" in model,
                    "has_temperature_sensor": True,
                }
            )
        elif model in ["RLNK-P420R", "RLNK-P420", "RLNK-P420R-SP"]:
            capabilities.update(
                {
                    "num_outlets": 4,
                    "supports_power_monitoring": True,
                    "supports_outlet_switching": True,
                    "supports_energy_monitoring": True,
                    "supports_outlet_scheduling": False,
                    "max_current": 20,
                    "has_surge_protection": "-SP" in model,
                    "has_temperature_sensor": True,
                }
            )

        # Extract additional capabilities from model name if not already set
        if "num_outlets" not in capabilities and isinstance(model, str):
            # Extract outlet count from model name (e.g., RLNK-920 -> 9 outlets)
            match = re.search(r"[P-](\d+)", model)
            if match:
                num_str = match.group(1)
                # First digit often indicates number of outlets
                if num_str and len(num_str) >= 1:
                    try:
                        capabilities["num_outlets"] = int(num_str[0])
                    except ValueError:
                        pass

        _LOGGER.debug("Model capabilities for %s: %s", model, capabilities)
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
            if not self._model or not self._serial_number:
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
        """Get detailed power metrics for a specific outlet."""
        if not self._connected:
            _LOGGER.debug(
                "Not connected, skipping outlet power data refresh for outlet %d",
                outlet_num,
            )
            return {}

        try:
            _LOGGER.debug("Getting detailed power metrics for outlet %d", outlet_num)
            response = await self.send_command(f"show outlets {outlet_num} details")

            if not response:
                _LOGGER.error("Failed to get outlet details - empty response")
                return {}

            # Parse the response to extract power metrics
            data = self._parse_outlet_details(response, outlet_num)
            return data

        except Exception as e:
            _LOGGER.error("Error getting power data for outlet %d: %s", outlet_num, e)
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
                self._buffer = b""  # Clear buffer on disconnect

    def _ensure_command_processor_running(self) -> None:
        """Ensure the command processor task is running."""
        if self._command_processor is None or self._command_processor.done():
            _LOGGER.debug("Starting command processor task")
            self._command_processor = asyncio.create_task(self._process_command_queue())
            # Add a done callback to log when the task completes
            self._command_processor.add_done_callback(
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
        if self._command_processor and not self._command_processor.done():
            _LOGGER.debug("Cancelling command processor task")
            self._command_processor.cancel()
            try:
                # Wait briefly for cancellation
                await asyncio.wait_for(
                    asyncio.shield(self._command_processor), timeout=1
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

    async def send_command(
        self, command: str, timeout: int = None, wait_time: float = 0.5
    ) -> str:
        """Send a command to the device and wait for the response."""
        if not command:
            return ""

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
                if self._buffer:
                    _LOGGER.debug(
                        "Clearing read buffer of %d bytes before sending command",
                        len(self._buffer),
                    )
                    self._buffer = b""

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
            self._command_processor = asyncio.create_task(self._process_command_queue())

    async def reconnect(self) -> bool:
        """Reconnect to the device if not already connected."""
        async with self._connection_lock:
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

        # Support specific RackLink prompt patterns based on diagnostic script findings
        patterns = [pattern]
        if pattern == b"#":
            # Add common alternative prompts if searching for default
            patterns.extend([b">", b"$", b":", b"RackLink>", b"admin>"])
        elif pattern == b"Username:":
            patterns = [b"Username:", b"login:"]  # Username prompts
        elif pattern == b"Password:":
            patterns = [b"Password:", b"password:"]  # Password prompts

        # Use existing buffer or create a new one
        buffer = self._buffer

        # Add a more sophisticated timeout handling with partial matches
        max_attempts = 20
        attempt_count = 0
        last_buffer_size = len(buffer)
        last_data_time = time.time()

        while time.time() - start_time < timeout:
            # Check if any pattern is already in buffer
            for ptn in patterns:
                if ptn in buffer:
                    pattern_index = buffer.find(ptn) + len(ptn)
                    result = buffer[:pattern_index]
                    # Save remaining data for next read
                    self._buffer = buffer[pattern_index:]
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
        self._buffer = buffer
        return buffer

    async def cycle_outlet(self, outlet_num: int) -> bool:
        """Cycle power for a specific outlet."""
        if not self._connected:
            _LOGGER.warning("Not connected, cannot cycle outlet %d", outlet_num)
            return False

        try:
            _LOGGER.info("Cycling power for outlet %d", outlet_num)

            # First turn the outlet off
            off_command = f"power outlets {outlet_num} off /y"
            await self.send_command(off_command)

            # Wait 5 seconds
            await asyncio.sleep(5)

            # Then turn it back on
            on_command = f"power outlets {outlet_num} on /y"
            await self.send_command(on_command)

            # Update internal state
            self._outlet_states[outlet_num] = True

            return True
        except Exception as e:
            _LOGGER.error("Error cycling outlet %d: %s", outlet_num, e)
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
                return False

        except Exception as e:
            _LOGGER.error("Error turning on outlet %d: %s", outlet_num, e)
            return False

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
                return False

        except Exception as e:
            _LOGGER.error("Error turning off outlet %d: %s", outlet_num, e)
            return False

    async def get_outlet_state(self, outlet_num: int) -> bool:
        """Get the current state of a specific outlet."""
        if not self._connected:
            _LOGGER.debug("Not connected, cannot get outlet state")
            return False

        try:
            _LOGGER.debug("Getting state for outlet %d", outlet_num)
            command = f"show outlets {outlet_num} details"
            response = await self.send_command(command)

            if not response:
                _LOGGER.error("No response getting outlet state")
                return False

            # Import parser here to avoid circular imports
            from .parser import parse_outlet_state

            # Use the robust parser function
            state = parse_outlet_state(response, outlet_num)

            if state is not None:
                # Update our internal state
                self._outlet_states[outlet_num] = state
                _LOGGER.debug(
                    "Outlet %d state: %s", outlet_num, "ON" if state else "OFF"
                )
                return state
            else:
                _LOGGER.error(
                    "Could not parse outlet state from response for outlet %d",
                    outlet_num,
                )
                # Return last known state if available, otherwise default to False
                return self._outlet_states.get(outlet_num, False)

        except Exception as e:
            _LOGGER.error("Error getting outlet state: %s", e)
            return False

    async def get_all_outlet_states(self, force_refresh: bool = False) -> dict:
        """Get all outlet states efficiently using a single command."""
        if not self._connected and not force_refresh:
            _LOGGER.debug("Not connected, cannot get outlet states")
            return self._outlet_states

        try:
            _LOGGER.debug("Getting all outlet states")
            command = "show outlets all"
            response = await self.send_command(command)

            if not response:
                _LOGGER.error("No response getting all outlet states")
                return self._outlet_states

            # Import parser here to avoid circular imports
            from .parser import parse_all_outlet_states, parse_outlet_names

            # Parse states and names
            outlet_states = parse_all_outlet_states(response)
            outlet_names = parse_outlet_names(response)

            # Update our cached data
            if outlet_states:
                self._outlet_states.update(outlet_states)
                _LOGGER.debug("Updated %d outlet states", len(outlet_states))

            if outlet_names:
                self._outlet_names.update(outlet_names)
                _LOGGER.debug("Updated %d outlet names", len(outlet_names))

            return self._outlet_states

        except Exception as e:
            _LOGGER.error("Error getting all outlet states: %s", e)
            return self._outlet_states

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
