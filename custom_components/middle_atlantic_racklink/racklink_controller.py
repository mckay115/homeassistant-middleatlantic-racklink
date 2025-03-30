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
            # Create a Future that will receive the data
            read_future = loop.create_future()

            # Define the read callback to be used with add_reader
            def _socket_read_callback():
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
                except Exception as e:
                    _LOGGER.debug("Socket read error: %s", e)
                    read_future.set_exception(e)

            # Add the socket reader
            loop.add_reader(self._socket.fileno(), _socket_read_callback)

            try:
                # Wait for the read to complete or timeout
                return await asyncio.wait_for(read_future, timeout=timeout)
            finally:
                # Always remove the reader when done
                loop.remove_reader(self._socket.fileno())

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
                _LOGGER.debug("Already connected to %s", self._host)
                return True

            _LOGGER.info("Connecting to %s:%s", self._host, self._port)

            # Reset the retry counter when we start a new connection attempt
            self._retry_count = 0

            try:
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
                # Get PDU details
                details_cmd = "show pdu details"
                details_response = await self.send_command(details_cmd)

                if details_response:
                    # Parse model
                    model_match = re.search(
                        r"Model:\s*(.+?)(?:\r|\n)",
                        details_response,
                        re.IGNORECASE,
                    )
                    if model_match:
                        self._model = model_match.group(1).strip()
                        _LOGGER.debug("Found PDU model: %s", self._model)

                    # Parse serial number
                    sn_match = re.search(
                        r"Serial Number:\s*(.+?)(?:\r|\n)",
                        details_response,
                        re.IGNORECASE,
                    )
                    if sn_match:
                        self._serial_number = sn_match.group(1).strip()
                        _LOGGER.debug("Found PDU serial: %s", self._serial_number)

                    # Parse firmware version
                    fw_match = re.search(
                        r"Firmware Version:\s*(.+?)(?:\r|\n)",
                        details_response,
                        re.IGNORECASE,
                    )
                    if fw_match:
                        self._firmware_version = fw_match.group(1).strip()
                        _LOGGER.debug("Found PDU firmware: %s", self._firmware_version)

                    # Parse name
                    name_match = re.search(
                        r"Name:\s*'(.+?)'",
                        details_response,
                        re.IGNORECASE,
                    )
                    if name_match:
                        self._pdu_name = name_match.group(1).strip()
                        _LOGGER.debug("Found PDU name: %s", self._pdu_name)

                # Get MAC address if we don't have it
                if not self._mac_address:
                    _LOGGER.debug("Getting network interface information")
                    net_cmd = "show network interface eth1"
                    net_response = await self.send_command(net_cmd)

                    if net_response:
                        mac_match = re.search(
                            r"MAC address:\s*(.+?)(?:\r|\n)",
                            net_response,
                            re.IGNORECASE,
                        )
                        if mac_match:
                            self._mac_address = mac_match.group(1).strip()
                            _LOGGER.debug(
                                "Found PDU MAC address: %s", self._mac_address
                            )

            except Exception as e:
                _LOGGER.error("Error getting device info: %s", e)
        else:
            _LOGGER.debug("Using cached device info")

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
        base_capabilities = MODEL_CAPABILITIES.get(model, MODEL_CAPABILITIES["DEFAULT"])

        # Start with base capabilities for this model
        capabilities = base_capabilities.copy()

        # For P series (newer models) we can infer some capabilities
        if model.startswith("RLNK-P"):
            capabilities.update(
                {
                    "supports_power_monitoring": True,
                    "supports_outlet_switching": True,
                    "supports_energy_monitoring": True,
                    "supports_outlet_scheduling": False,
                    "max_current": 20,  # Most models are 20A, adjust if needed
                    "has_surge_protection": False,
                    "has_temperature_sensor": True,  # Most Pro models have temperature sensing
                }
            )

        # For RLNK-P920R model specifically
        if model == "RLNK-P920R":
            capabilities.update(
                {
                    "num_outlets": 9,
                    "supports_power_monitoring": True,
                    "supports_outlet_switching": True,
                    "supports_energy_monitoring": True,
                    "supports_outlet_scheduling": False,
                    "max_current": 20,
                    "has_surge_protection": False,
                    "has_temperature_sensor": True,
                }
            )

        # Extract additional capabilities from model name
        if "num_outlets" not in capabilities and isinstance(model, str):
            # Extract outlet count from model name (e.g., RLNK-920 -> 9 outlets)
            match = re.search(r"P(\d+)", model)
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
        """Update PDU state - this is called periodically by the coordinator."""
        try:
            if not self._connected:
                _LOGGER.debug("Not connected during update, attempting to connect")
                if not await self.reconnect():
                    _LOGGER.warning("Failed to connect during update")
                    return False

            # Update all outlet states efficiently with a single command
            _LOGGER.debug("Updating all outlet states with 'show outlets all'")
            try:
                all_outlets_response = await self.send_command("show outlets all")
                if all_outlets_response:
                    # Parse the response to extract all outlet states
                    outlet_pattern = r"Outlet (\d+):[^\n]*\n\s+(\w+)"
                    matches = re.findall(outlet_pattern, all_outlets_response)

                    for match in matches:
                        outlet_num = int(match[0])
                        state = match[1].lower() == "on"
                        self._outlet_states[outlet_num] = state

                        # Default all outlets to non-critical = False
                        if outlet_num not in self._outlet_non_critical:
                            self._outlet_non_critical[outlet_num] = False

                        # Extract outlet name if available
                        name_match = re.search(
                            rf"Outlet {outlet_num}: ([^\n]+)", all_outlets_response
                        )
                        if name_match:
                            self._outlet_names[outlet_num] = name_match.group(1).strip()

                    _LOGGER.debug(
                        "Updated %d outlet states from 'show outlets all'", len(matches)
                    )
                else:
                    _LOGGER.warning("No response from 'show outlets all' command")
            except Exception as e:
                _LOGGER.error("Error updating outlet states: %s", e)

            # If we have power data collection enabled, update detailed metrics
            # This still needs individual commands but we can limit frequency
            if hasattr(self, "_collect_power_data") and self._collect_power_data:
                # Get detailed power data for a subset of outlets each time
                # to spread the load and avoid too many commands at once
                await self.get_all_power_data(
                    sample_size=3
                )  # Get power data for 3 outlets per update

            # Try to get global PDU sensor data if available
            try:
                if self.get_model_capabilities().get(
                    "supports_power_monitoring", False
                ):
                    # Get voltage from any outlet as they all report the same line voltage
                    if self._outlet_voltage and 1 in self._outlet_voltage:
                        self._sensors["voltage"] = self._outlet_voltage[1]

                    # Try to get global PDU metrics if available
                    pdu_stats_response = await self.send_command("show pdu power")
                    if pdu_stats_response:
                        # Parse power data
                        power_match = re.search(
                            r"Power:\s*([\d.]+)\s*W", pdu_stats_response
                        )
                        if power_match:
                            try:
                                self._sensors["power"] = float(power_match.group(1))
                            except ValueError:
                                pass

                        # Parse current
                        current_match = re.search(
                            r"Current:\s*([\d.]+)\s*A", pdu_stats_response
                        )
                        if current_match:
                            try:
                                self._sensors["current"] = float(current_match.group(1))
                            except ValueError:
                                pass

                        # Parse energy
                        energy_match = re.search(
                            r"Energy:\s*([\d.]+)\s*kWh", pdu_stats_response
                        )
                        if energy_match:
                            try:
                                self._sensors["energy"] = (
                                    float(energy_match.group(1)) * 1000
                                )  # Convert to Wh
                            except ValueError:
                                pass

                        # Parse power factor
                        pf_match = re.search(
                            r"Power Factor:\s*([\d.]+)", pdu_stats_response
                        )
                        if pf_match:
                            try:
                                self._sensors["power_factor"] = float(pf_match.group(1))
                            except ValueError:
                                pass

                        # Parse frequency
                        freq_match = re.search(
                            r"Frequency:\s*([\d.]+)\s*Hz", pdu_stats_response
                        )
                        if freq_match:
                            try:
                                self._sensors["frequency"] = float(freq_match.group(1))
                            except ValueError:
                                pass

                # Get temperature if supported
                if self.get_model_capabilities().get("has_temperature_sensor", False):
                    temp_response = await self.send_command("show pdu temperature")
                    if temp_response:
                        temp_match = re.search(
                            r"Temperature:\s*([\d.]+)\s*C", temp_response
                        )
                        if temp_match:
                            try:
                                self._sensors["temperature"] = float(
                                    temp_match.group(1)
                                )
                            except ValueError:
                                pass
            except Exception as e:
                _LOGGER.error("Error updating PDU sensor data: %s", e)

            # Mark as available since we could communicate
            self._available = True
            self._last_update = time.time()

            return True

        except Exception as e:
            _LOGGER.error("Error in update: %s", e)
            self._available = False
            return False

    def _parse_outlet_details(self, response: str, outlet_num: int) -> dict:
        """
        Parse outlet details response to extract power metrics.

        Based on improvements from diagnostic script.
        """
        outlet_data = {"outlet_number": outlet_num}

        # Parse power state (for switch entities)
        # Example line: "Power state:        On"
        state_match = re.search(r"Power state:\s*(\w+)", response, re.IGNORECASE)
        if state_match:
            state = state_match.group(1)
            outlet_data["state"] = state.lower()
            self._outlet_states[outlet_num] = state.lower() == "on"
            _LOGGER.debug("Parsed outlet %s state: %s", outlet_num, state)
        else:
            _LOGGER.debug("Could not find outlet state in response")

        # Parse current (for current sensor)
        # Example line: "RMS Current:        0.114 A"
        current_match = re.search(
            r"RMS Current:\s*([\d.]+)\s*A", response, re.IGNORECASE
        )
        if current_match:
            try:
                current = float(current_match.group(1))
                outlet_data["current"] = current
                self._outlet_current[outlet_num] = current
                _LOGGER.debug("Parsed outlet %s current: %s A", outlet_num, current)
            except ValueError:
                _LOGGER.error(
                    "Could not convert current value: %s", current_match.group(1)
                )

        # Parse voltage (for voltage sensor)
        # Example line: "RMS Voltage:        122.1 V"
        voltage_match = re.search(
            r"RMS Voltage:\s*([\d.]+)\s*V", response, re.IGNORECASE
        )
        if voltage_match:
            try:
                voltage = float(voltage_match.group(1))
                outlet_data["voltage"] = voltage
                self._outlet_voltage[outlet_num] = voltage
                _LOGGER.debug("Parsed outlet %s voltage: %s V", outlet_num, voltage)
            except ValueError:
                _LOGGER.error(
                    "Could not convert voltage value: %s", voltage_match.group(1)
                )

        # Parse power (for power sensor)
        # Example line: "Active Power:        7 W"
        power_match = re.search(
            r"Active Power:\s*([\d.]+)\s*W", response, re.IGNORECASE
        )
        if power_match:
            try:
                power = float(power_match.group(1))
                outlet_data["power"] = power
                self._outlet_power[outlet_num] = power
                _LOGGER.debug("Parsed outlet %s power: %s W", outlet_num, power)
            except ValueError:
                _LOGGER.error("Could not convert power value: %s", power_match.group(1))

        # Parse energy (for energy sensor)
        # Example line: "Active Energy:        632210 Wh"
        energy_match = re.search(
            r"Active Energy:\s*([\d.]+)\s*Wh", response, re.IGNORECASE
        )
        if energy_match:
            try:
                energy = float(energy_match.group(1))
                outlet_data["energy"] = energy
                self._outlet_energy[outlet_num] = energy
                _LOGGER.debug("Parsed outlet %s energy: %s Wh", outlet_num, energy)
            except ValueError:
                _LOGGER.error(
                    "Could not convert energy value: %s", energy_match.group(1)
                )

        # Parse power factor
        # Example line: "Power Factor:        0.53"
        pf_match = re.search(r"Power Factor:\s*([\d.]+)", response, re.IGNORECASE)
        if pf_match:
            try:
                power_factor = float(pf_match.group(1))
                outlet_data["power_factor"] = power_factor
                self._outlet_power_factor[outlet_num] = power_factor
                _LOGGER.debug(
                    "Parsed outlet %s power factor: %s", outlet_num, power_factor
                )
            except ValueError:
                _LOGGER.error("Could not convert power factor: %s", pf_match.group(1))

        # Parse line frequency
        # Example line: "Line Frequency:        60.0 Hz"
        freq_match = re.search(
            r"Line Frequency:\s*([\d.]+)\s*Hz", response, re.IGNORECASE
        )
        if freq_match:
            try:
                frequency = float(freq_match.group(1))
                outlet_data["frequency"] = frequency
                _LOGGER.debug(
                    "Parsed outlet %s frequency: %s Hz", outlet_num, frequency
                )
            except ValueError:
                _LOGGER.error("Could not convert frequency: %s", freq_match.group(1))

        return outlet_data

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
            missing_power_data = [
                o for o in all_outlets if o not in self._outlet_power_data
            ]

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
        """Send a command to the device and wait for the response.

        Args:
            command: The command to send
            timeout: Command timeout in seconds
            wait_time: Time to wait after sending command before reading response
        """
        if not command:
            return ""

        command_timeout = timeout or self._command_timeout
        command_with_newline = f"{command}\r\n"

        try:
            if not self._connected:
                _LOGGER.debug("Not connected while sending command: %s", command)
                await self.reconnect()
                if not self._connected:
                    _LOGGER.warning("Could not reconnect to send command: %s", command)
                    return ""

            _LOGGER.debug("Sending command: %s (timeout: %s)", command, command_timeout)

            # Flush any data in the buffer before sending
            if self._buffer:
                _LOGGER.debug(
                    "Clearing read buffer of %d bytes before sending command",
                    len(self._buffer),
                )
                self._buffer = b""

            # Send the command
            await self._socket_write(command_with_newline.encode())

            # Wait for device to process the command
            # Some commands need more time, especially power control commands
            if "power outlets" in command:
                # Power commands need more time
                await asyncio.sleep(wait_time * 2)
            else:
                await asyncio.sleep(wait_time)

            # Read response until we get the command prompt
            response = await self._socket_read_until(b"#", timeout=command_timeout)

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

            return response_text

        except Exception as e:
            _LOGGER.error("Error sending command '%s': %s", command, e)
            return ""

    async def _process_command_queue(self):
        """Process commands from the queue."""
        _LOGGER.debug("Starting command queue processor")

        while not self._shutdown_requested:
            try:
                # Check if we're connected
                if not self._connected:
                    _LOGGER.debug("Not connected, waiting before processing commands")
                    await asyncio.sleep(1)
                    continue

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

                    # Set the result if we have a future
                    if future is not None and not future.done():
                        future.set_result(response)
                except Exception as e:
                    _LOGGER.error("Error executing queued command '%s': %s", command, e)
                    # Set exception on future if we have one
                    if future is not None and not future.done():
                        future.set_exception(e)
                finally:
                    # Mark task as done regardless of outcome
                    self._command_queue.task_done()

                # Small delay to prevent flooding the device
                await asyncio.sleep(0.1)

            except asyncio.CancelledError:
                _LOGGER.debug("Command processor was cancelled")
                break
            except Exception as e:
                _LOGGER.error("Unexpected error in command processor: %s", e)
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

            # Try different port numbers if the default doesn't work
            potential_ports = [self._port]

            # If the port is the default (6000), also try some alternative common ports
            if self._port == 6000:
                potential_ports.extend([23, 22, 2000, 4000, 8000, 6001])

            # Try each port in sequence
            for port in potential_ports:
                if port != self._port:
                    _LOGGER.info("Trying alternative port %s for %s", port, self._host)

                    # Save the current port we're trying
                    current_port = self._port
                    self._port = port

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
                            if port != current_port:
                                _LOGGER.warning(
                                    "Successfully connected to %s using alternative port %s instead of %s",
                                    self._host,
                                    port,
                                    current_port,
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
            self._port = current_port
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

        while time.time() - start_time < timeout:
            # Check if any pattern is already in buffer
            for ptn in patterns:
                if ptn in buffer:
                    pattern_index = buffer.find(ptn) + len(ptn)
                    result = buffer[:pattern_index]
                    # Save remaining data for next read
                    self._buffer = buffer[pattern_index:]
                    return result

            # Calculate remaining time
            remaining_time = timeout - (time.time() - start_time)
            if remaining_time <= 0:
                break

            # Read more data with shorter timeouts for responsiveness
            read_timeout = min(0.5, remaining_time)

            try:
                data = await self._socket_read(timeout=read_timeout)

                if not data:
                    # No data received in this read attempt
                    if time.time() - start_time >= timeout:
                        # Overall timeout reached
                        _LOGGER.debug("Timeout reached while waiting for pattern")
                        break

                    # Brief pause before next attempt to avoid CPU spinning
                    await asyncio.sleep(0.1)
                    continue

                # Append new data to buffer
                buffer += data
                _LOGGER.debug(
                    "Read %d bytes from socket, buffer now %d bytes, looking for %s",
                    len(data),
                    len(buffer),
                    pattern,
                )

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
