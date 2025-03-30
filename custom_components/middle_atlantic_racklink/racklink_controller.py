"""Controller for Middle Atlantic Racklink PDU devices."""

import asyncio
import logging
import re
import socket
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple, List

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
        self._outlet_power_data = {}
        self._sensor_values = {}
        self._last_update = None

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
        """Get device information (model, firmware, etc)."""
        if not self.connected:
            _LOGGER.debug("Not connected, can't get device info")
            return self._pdu_info

        try:
            # Check if we need to get info (first run)
            if not self._model:
                _LOGGER.info("Getting PDU information")

                # Try different commands to extract system info
                commands_to_try = [
                    "show system info",
                    "show system",
                    "system info",
                    "info",
                    "show info",
                ]

                system_info = None
                for cmd in commands_to_try:
                    _LOGGER.debug(f"Trying command: {cmd}")
                    response = await self.send_command(cmd)
                    if (
                        response and len(response) > 20
                    ):  # Ensure we got a meaningful response
                        system_info = response
                        _LOGGER.debug(f"Got response from command: {cmd}")
                        break
                    await asyncio.sleep(0.5)  # Brief delay between commands

                if system_info:
                    _LOGGER.debug("System info response: %s", system_info)

                    # Try multiple different patterns for model information
                    model_patterns = [
                        r"Model Name:\s*([^\r\n]+)",
                        r"Model:\s*([^\r\n]+)",
                        r"Product( type)?:\s*([^\r\n]+)",
                        r"Type:\s*([^\r\n]+)",
                    ]

                    for pattern in model_patterns:
                        model_match = re.search(pattern, system_info)
                        if model_match:
                            # If we have multiple capturing groups, use the last one
                            group_index = len(model_match.groups())
                            self._model = model_match.group(group_index).strip()
                            _LOGGER.debug("Found PDU model: %s", self._model)
                            break

                    # If we still don't have a model, try a broader search
                    if not self._model:
                        model_lines = re.findall(r"[Mm]odel.*", system_info)
                        if model_lines:
                            self._model = model_lines[0].strip()
                            _LOGGER.debug("Found PDU model from line: %s", self._model)

                    # Try multiple patterns for firmware
                    firmware_patterns = [
                        r"Firmware [Vv]ersion:\s*([^\r\n]+)",
                        r"Firmware:\s*([^\r\n]+)",
                        r"Version:\s*([^\r\n]+)",
                        r"FW( Version)?:\s*([^\r\n]+)",
                    ]

                    for pattern in firmware_patterns:
                        firmware_match = re.search(pattern, system_info)
                        if firmware_match:
                            # If we have multiple capturing groups, use the last one
                            group_index = len(firmware_match.groups())
                            self._firmware_version = firmware_match.group(
                                group_index
                            ).strip()
                            _LOGGER.debug(
                                "Found PDU firmware: %s", self._firmware_version
                            )
                            break

                    # Try multiple patterns for serial number
                    serial_patterns = [
                        r"Serial [Nn]umber:\s*([^\r\n]+)",
                        r"Serial:\s*([^\r\n]+)",
                        r"SN:\s*([^\r\n]+)",
                    ]

                    for pattern in serial_patterns:
                        serial_match = re.search(pattern, system_info)
                        if serial_match:
                            self._serial_number = serial_match.group(1).strip()
                            _LOGGER.debug("Found PDU serial: %s", self._serial_number)
                            break
                else:
                    _LOGGER.warning("Could not get system info from any command")

                # If we still don't have essential information, set reasonable defaults
                if not self._model:
                    self._model = "RackLink PDU"
                    _LOGGER.debug("Using default model name: %s", self._model)

                if not self._serial_number:
                    self._serial_number = f"RLNK_{self._host.replace('.', '_')}"
                    _LOGGER.debug("Generated PDU serial: %s", self._serial_number)

                if not self._firmware_version:
                    self._firmware_version = "Unknown"

                # Try to get outlet information to determine outlet count
                try:
                    outlet_commands = ["show outlets", "outlets", "show outlet"]
                    outlets_info = None

                    for cmd in outlet_commands:
                        response = await self.send_command(cmd)
                        if response and len(response) > 20:
                            outlets_info = response
                            break
                        await asyncio.sleep(0.5)

                    if outlets_info:
                        # Try different patterns to count outlets
                        outlet_patterns = [
                            r"^\s*\d+\s+\|",  # Pattern like "  1 |"
                            r"Outlet\s+(\d+)",  # Pattern like "Outlet 1"
                            r"^\s*(\d+)[\s-]+\w+",  # Pattern like "1 - OutletName"
                        ]

                        for pattern in outlet_patterns:
                            outlet_matches = re.findall(
                                pattern, outlets_info, re.MULTILINE
                            )
                            if outlet_matches:
                                num_outlets = len(outlet_matches)
                                _LOGGER.info("Found %s outlets", num_outlets)
                                break
                except Exception as e:
                    _LOGGER.error("Error getting outlet information: %s", e)

                # Update the pdu_info dictionary
                self._pdu_info = {
                    "model": self._model,
                    "firmware": self._firmware_version,
                    "serial": self._serial_number,
                    "name": self._pdu_name,
                }

            # Ensure we have basic information to satisfy config_flow
            if "model" not in self._pdu_info or not self._pdu_info["model"]:
                self._pdu_info["model"] = self._model or "RackLink PDU"

            if "serial" not in self._pdu_info or not self._pdu_info["serial"]:
                self._pdu_info["serial"] = (
                    self._serial_number or f"RLNK_{self._host.replace('.', '_')}"
                )

            return self._pdu_info

        except Exception as e:
            _LOGGER.error("Error getting device info: %s", e)
            # Make sure we still return valid info even after an error
            if (
                not self._pdu_info
                or "model" not in self._pdu_info
                or not self._pdu_info["model"]
            ):
                self._pdu_info = {
                    "model": "RackLink PDU",
                    "firmware": "Unknown",
                    "serial": f"RLNK_{self._host.replace('.', '_')}",
                    "name": self._pdu_name or "Middle Atlantic RackLink",
                }
            return self._pdu_info

    def get_model_capabilities(self) -> Dict[str, Any]:
        """Get capabilities based on model number."""
        # Default capabilities
        capabilities = {
            "num_outlets": 8,  # Default to 8 outlets
            "supports_power_monitoring": True,
            "supports_outlet_switching": True,
            "supports_energy_monitoring": True,
            "supports_outlet_scheduling": False,  # Most models don't support this
            "max_current": 15,  # Default to 15A
            "has_surge_protection": False,
            "has_temperature_sensor": True,
        }

        # Update based on model
        model = self._model

        if not model or model == "AUTO_DETECT" or model == "Racklink PDU":
            # Use controller response to determine outlet count
            outlet_count = (
                max(list(self._outlet_states.keys())) if self._outlet_states else 8
            )
            capabilities["num_outlets"] = outlet_count
            _LOGGER.debug(
                "Auto-detected %d outlets from device responses", outlet_count
            )
            return capabilities

        # RLNK-P415: 4 outlets, 15A
        if "P415" in model:
            capabilities["num_outlets"] = 4
            capabilities["max_current"] = 15

        # RLNK-P420: 4 outlets, 20A
        elif "P420" in model:
            capabilities["num_outlets"] = 4
            capabilities["max_current"] = 20

        # RLNK-P915R: 9 outlets, 15A
        elif "P915R" in model and "SP" not in model:
            capabilities["num_outlets"] = 9
            capabilities["max_current"] = 15

        # RLNK-P915R-SP: 9 outlets, 15A, surge protection
        elif "P915R-SP" in model:
            capabilities["num_outlets"] = 9
            capabilities["max_current"] = 15
            capabilities["has_surge_protection"] = True

        # RLNK-P920R: 9 outlets, 20A
        elif "P920R" in model and "SP" not in model:
            capabilities["num_outlets"] = 9
            capabilities["max_current"] = 20

        # RLNK-P920R-SP: 9 outlets, 20A, surge protection
        elif "P920R-SP" in model:
            capabilities["num_outlets"] = 9
            capabilities["max_current"] = 20
            capabilities["has_surge_protection"] = True
        else:
            # Try to extract number from model string if it follows a pattern
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
        """Update all data from the device."""
        if not self._connected:
            _LOGGER.debug("Not connected during update, attempting reconnect")
            await self.reconnect()
            if not self._connected:
                _LOGGER.warning("Still not connected after reconnect attempt")
                return False

        _LOGGER.debug("Updating all data from PDU")
        try:
            # Get basic device info if needed
            if not self._model:
                await self.get_device_info()

            # Update outlet states efficiently first (this is lightweight)
            states_success = await self.get_all_outlet_states(force_refresh=True)

            # Then update power data for a subset of outlets (to avoid overwhelming device)
            # This is more detailed and heavier on the device
            power_success = await self.get_all_power_data(sample_size=3)

            # Update main sensor values if needed
            sensor_success = await self.get_sensor_values()

            # Update timestamp
            self._last_update = time.time()
            self._available = True

            return states_success

        except Exception as e:
            _LOGGER.error("Error during update: %s", e)
            return False

    def _parse_outlet_details(self, response: str, outlet_num: int) -> dict:
        """Parse outlet details from response text."""
        data = {}

        # Extract state (ON/OFF)
        state_match = re.search(r"Power state:\s*(\w+)", response, re.IGNORECASE)
        if state_match:
            state = state_match.group(1).lower()
            self._outlet_states[outlet_num] = state == "on"
            data["state"] = state == "on"

        # Extract current (A)
        current_match = re.search(
            r"RMS Current:\s*([\d.]+)\s*A", response, re.IGNORECASE
        )
        if current_match:
            try:
                current = float(current_match.group(1))
                self._outlet_power_data[outlet_num] = current
                data["current"] = current
            except ValueError:
                _LOGGER.error(
                    "Failed to parse current value: %s", current_match.group(1)
                )

        # Extract voltage (V)
        voltage_match = re.search(
            r"RMS Voltage:\s*([\d.]+)\s*V", response, re.IGNORECASE
        )
        if voltage_match:
            try:
                voltage = float(voltage_match.group(1))
                self._outlet_power_data[outlet_num] = voltage
                data["voltage"] = voltage
            except ValueError:
                _LOGGER.error(
                    "Failed to parse voltage value: %s", voltage_match.group(1)
                )

        # Extract active power (W)
        power_match = re.search(
            r"Active Power:\s*([\d.]+)\s*W", response, re.IGNORECASE
        )
        if power_match:
            try:
                power = float(power_match.group(1))
                self._outlet_power_data[outlet_num] = power
                data["power"] = power
            except ValueError:
                _LOGGER.error("Failed to parse power value: %s", power_match.group(1))

        # Extract energy consumption (Wh)
        energy_match = re.search(
            r"Active Energy:\s*([\d.]+)\s*Wh", response, re.IGNORECASE
        )
        if energy_match:
            try:
                energy = float(energy_match.group(1))
                self._outlet_power_data[outlet_num] = energy
                data["energy"] = energy
            except ValueError:
                _LOGGER.error("Failed to parse energy value: %s", energy_match.group(1))

        # Extract power factor
        pf_match = re.search(r"Power Factor:\s*([\d.]+)", response, re.IGNORECASE)
        if pf_match and pf_match.group(1) != "---":
            try:
                power_factor = float(pf_match.group(1))
                self._outlet_power_data[outlet_num] = power_factor
                data["power_factor"] = power_factor
            except ValueError:
                _LOGGER.error(
                    "Failed to parse power factor value: %s", pf_match.group(1)
                )

        # Extract apparent power (VA)
        apparent_match = re.search(
            r"Apparent Power:\s*([\d.]+)\s*VA", response, re.IGNORECASE
        )
        if apparent_match:
            try:
                apparent_power = float(apparent_match.group(1))
                self._outlet_power_data[outlet_num] = apparent_power
                data["apparent_power"] = apparent_power
            except ValueError:
                _LOGGER.error(
                    "Failed to parse apparent power value: %s", apparent_match.group(1)
                )

        # Extract line frequency (Hz)
        freq_match = re.search(
            r"Line Frequency:\s*([\d.]+)\s*Hz", response, re.IGNORECASE
        )
        if freq_match:
            try:
                frequency = float(freq_match.group(1))
                self._outlet_power_data[outlet_num] = frequency
                data["frequency"] = frequency
            except ValueError:
                _LOGGER.error(
                    "Failed to parse frequency value: %s", freq_match.group(1)
                )

        # Extract outlet name if present
        name_match = re.search(r"Outlet\s+\d+\s*-\s*([^:]+):", response)
        if name_match:
            outlet_name = name_match.group(1).strip()
            if outlet_name:
                self._outlet_names[outlet_num] = outlet_name
                data["name"] = outlet_name

        # Extract non-critical flag
        nc_match = re.search(r"Non critical:\s*(\w+)", response, re.IGNORECASE)
        if nc_match:
            non_critical = nc_match.group(1).lower() == "true"
            data["non_critical"] = non_critical

        return data

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

            # Try different prompt patterns the device might use
            prompt_patterns = [b">", b"#", b"$", b":", b"RackLink>", b"admin>"]

            # Read initial welcome or prompt
            _LOGGER.debug("Reading initial data from device")
            initial_data = None

            try:
                # First just try to read whatever data is available
                def socket_read():
                    try:
                        self._socket.settimeout(3.0)
                        return self._socket.recv(4096)
                    except socket.timeout:
                        _LOGGER.debug("Initial read timed out, which might be normal")
                        return b""
                    except Exception as e:
                        _LOGGER.error("Socket read error: %s", e)
                        return b""

                initial_data = await asyncio.to_thread(socket_read)
                _LOGGER.debug(
                    "Initial data received (%d bytes): %s",
                    len(initial_data),
                    initial_data[:100] if initial_data else "None",
                )

                # If we got data and it contains a prompt, we might already be connected
                if initial_data:
                    for prompt in prompt_patterns:
                        if prompt in initial_data:
                            _LOGGER.debug(
                                "Detected prompt '%s' in initial data", prompt
                            )
                            self._buffer = initial_data  # Store remaining data
                            return True

            except Exception as e:
                _LOGGER.warning("Error reading initial data: %s", e)
                # Continue with login attempts even if initial read fails

            # Try sending various login commands
            login_attempts = [
                (b"\r\n", 2),  # Empty line
                (b"admin\r\n", 2),  # Try admin username
                (self._username.encode() + b"\r\n", 2),  # Try configured username
                (self._password.encode() + b"\r\n", 2),  # Try password
                (b"enable\r\n", 2),  # Try enable command
            ]

            # Try each login command
            for cmd, timeout in login_attempts:
                _LOGGER.debug("Sending login sequence: %s", cmd)
                try:
                    # Send command
                    def send_cmd(sock, data):
                        try:
                            sock.sendall(data)
                            return True
                        except Exception as e:
                            _LOGGER.error("Error sending login command: %s", e)
                            return False

                    await asyncio.to_thread(send_cmd, self._socket, cmd)

                    # Read response
                    def read_response():
                        try:
                            self._socket.settimeout(timeout)
                            return self._socket.recv(4096)
                        except socket.timeout:
                            return b""
                        except Exception as e:
                            _LOGGER.error("Socket read error during login: %s", e)
                            return b""

                    response = await asyncio.to_thread(read_response)

                    # Check if we got a prompt in the response
                    if response:
                        _LOGGER.debug(
                            "Got response (%d bytes): %s",
                            len(response),
                            response[:100] if response else "None",
                        )

                        # Check if any prompt is found
                        for prompt in prompt_patterns:
                            if prompt in response:
                                _LOGGER.info(
                                    "Successfully logged in, found prompt: %s", prompt
                                )
                                self._buffer = response  # Store remaining data
                                return True

                except Exception as e:
                    _LOGGER.warning("Login attempt failed: %s", e)
                    await asyncio.sleep(0.5)  # Brief delay before next attempt

            # As a last resort, just assume we're connected if we got any response at all
            if initial_data or response:
                _LOGGER.warning(
                    "Did not find expected prompt, but received data. Assuming connected."
                )
                if response:  # Save last response to buffer
                    self._buffer = response
                elif initial_data:
                    self._buffer = initial_data
                return True

            _LOGGER.error("Failed to login - could not detect any valid prompt")
            return False

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

    async def send_command(self, command: str, timeout: int = None) -> str:
        """Send a command to the device and wait for the response."""
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

            # Send the command directly
            try:
                await self._socket_write(command_with_newline.encode())
            except Exception as e:
                _LOGGER.error("Failed to send command: %s", e)
                # Try to reconnect and retry once
                if await self.reconnect():
                    _LOGGER.debug("Reconnected, retrying command: %s", command)
                    await self._socket_write(command_with_newline.encode())
                else:
                    return ""

            # Use a longer timeout for the first read to account for slow devices
            initial_timeout = min(command_timeout, 5)

            # Wait for the prompt to return, which indicates command completion
            try:
                response = await self._socket_read(timeout=command_timeout)
            except Exception as e:
                _LOGGER.warning(
                    "Error reading response, trying direct socket read: %s", e
                )
                # Fallback to direct socket read
                try:

                    def direct_read():
                        try:
                            self._socket.settimeout(command_timeout)
                            chunks = []
                            start_time = time.time()
                            while time.time() - start_time < command_timeout:
                                try:
                                    chunk = self._socket.recv(1024)
                                    if not chunk:
                                        break
                                    chunks.append(chunk)
                                    # If we see what looks like a prompt, we can stop
                                    if any(
                                        prompt in chunk
                                        for prompt in [b">", b"#", b"$", b":"]
                                    ):
                                        break
                                except socket.timeout:
                                    break
                            return b"".join(chunks)
                        except Exception as e:
                            _LOGGER.error("Direct socket read error: %s", e)
                            return b""

                    response = await asyncio.to_thread(direct_read)
                except Exception as fallback_e:
                    _LOGGER.error("Fallback read also failed: %s", fallback_e)
                    return ""

            # Clean up the response
            if response:
                # Convert to string
                response_str = response.decode(errors="ignore")

                # Remove echo of the command itself from the beginning
                lines = response_str.splitlines()
                if len(lines) > 0 and (
                    command in lines[0] or command.lower() in lines[0].lower()
                ):
                    response_str = "\n".join(lines[1:])

                # Remove ANSI escape sequences
                response_str = re.sub(r"\x1b\[[0-9;]*[mK]", "", response_str)

                # Remove trailing prompt characters common on network devices
                response_str = re.sub(r"[>#\$\:]$", "", response_str).strip()

                _LOGGER.debug(
                    "Command response (truncated): %s",
                    response_str[:100] + ("..." if len(response_str) > 100 else ""),
                )
                return response_str
            else:
                _LOGGER.warning("Empty response for command: %s", command)
                return ""

        except asyncio.TimeoutError:
            _LOGGER.warning("Command timed out: %s", command)
            return ""
        except Exception as e:
            _LOGGER.error("Error sending command: %s - %s", command, e)
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
            pattern = b">"  # Default to common prompt character

        timeout = timeout or self._socket_timeout
        start_time = time.time()

        # Support multiple prompt patterns
        patterns = [pattern]
        if pattern == b">":
            # Add common alternative prompts if searching for default
            patterns.extend([b"#", b"$", b":", b"RackLink>", b"admin>"])

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
                    "Read %d bytes from socket, buffer now %d bytes",
                    len(data),
                    len(buffer),
                )

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
