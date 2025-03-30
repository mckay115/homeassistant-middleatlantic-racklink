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
        model: str = None,
        socket_timeout: int = 5,
        command_timeout: int = 10,
    ):
        """Initialize the controller."""
        self._host = host
        self._port = port
        self._username = username or ""
        self._password = password or ""
        self._model = model
        self._socket_timeout = socket_timeout
        self._command_timeout = command_timeout
        self._connection_lock = asyncio.Lock()
        self._connection_timeout = 20  # Longer timeout for initial connection
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 3
        self._last_error = None
        self._last_error_time = None
        self._command_processor_task = None

        # Socket connection
        self._socket = None
        self._reader = None
        self._writer = None
        self._connected = False
        self._available = False
        self._socket_timeout = socket_timeout
        self._command_queue = asyncio.Queue()
        self._command_process_task = None
        self._last_error = None
        self._last_error_time = None
        self._connection_lock = asyncio.Lock()

        # Model information
        self.pdu_name = "Middle Atlantic Racklink"
        self.pdu_model = None
        self.pdu_serial = None
        self.pdu_firmware = None
        self.pdu_info = {}

        # Outlet status tracking
        self.outlet_states = {}  # outlet_num -> ON/OFF status
        self.outlet_names = {}  # outlet_num -> name

        # Outlet power metrics
        self.outlet_current = {}  # outlet_num -> float (A)
        self.outlet_power = {}  # outlet_num -> float (W)
        self.outlet_energy = {}  # outlet_num -> float (Wh)
        self.outlet_voltage = {}  # outlet_num -> float (V)
        self.outlet_power_factor = {}  # outlet_num -> float (decimal)
        self.outlet_apparent_power = {}  # outlet_num -> float (VA)
        self.outlet_line_frequency = {}  # outlet_num -> float (Hz)
        self.outlet_non_critical = {}  # outlet_num -> bool

        # PDU sensors
        self.sensors = {
            "temperature": None,
            "current": None,
            "voltage": None,
            "power": None,
            "energy": None,
            "power_factor": None,
            "frequency": None,
        }

        # Status tracking
        self._last_update = 0
        self._shutdown_requested = False
        self._read_buffer = b""  # Buffer for socket reads

        # Start connection in background
        self._connection_task = asyncio.create_task(self._background_connect())

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

    async def _create_socket_connection(self):
        """Create a socket connection in a separate thread to avoid blocking."""
        try:
            # Create socket connection in a separate thread
            _LOGGER.debug("Creating socket connection to %s:%s", self._host, self._port)

            def connect_socket():
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(self._socket_timeout)
                    sock.connect((self._host, self._port))
                    return sock
                except Exception as e:
                    _LOGGER.error("Socket connection error: %s", e)
                    return None

            result = await asyncio.to_thread(connect_socket)

            if result:
                _LOGGER.debug("Successfully created socket connection")
            else:
                _LOGGER.error("Failed to create socket connection - returned None")
            return result
        except Exception as e:
            _LOGGER.error("Failed to create socket connection: %s", e)
            return None

    async def _socket_read_until(self, pattern: bytes, timeout: int = None) -> bytes:
        """Read from socket connection until pattern is found."""
        if not self._socket:
            raise ConnectionError("No socket connection available")

        if pattern is None:
            raise ValueError("Pattern cannot be None")

        timeout = timeout or self._socket_timeout
        start_time = time.time()

        try:
            # Use existing buffer or create a new one
            buffer = self._read_buffer

            while time.time() - start_time < timeout:
                # Check if pattern is already in buffer
                if pattern in buffer:
                    pattern_index = buffer.find(pattern) + len(pattern)
                    result = buffer[:pattern_index]
                    # Save remaining data for next read
                    self._read_buffer = buffer[pattern_index:]
                    return result

                # Set socket timeout
                self._socket.settimeout(min(0.5, timeout - (time.time() - start_time)))

                try:
                    # Read more data from socket
                    def read_socket():
                        try:
                            return self._socket.recv(1024)
                        except socket.timeout:
                            return b""
                        except Exception as e:
                            _LOGGER.error("Socket read error: %s", e)
                            raise

                    data = await asyncio.to_thread(read_socket)

                    if not data:
                        # Connection closed or timeout
                        if time.time() - start_time >= timeout:
                            _LOGGER.warning("Socket read timeout")
                            break
                        await asyncio.sleep(0.1)  # Short sleep to prevent CPU spinning
                        continue

                    buffer += data
                except asyncio.CancelledError:
                    _LOGGER.warning("Socket read operation was cancelled")
                    raise
                except Exception as e:
                    _LOGGER.error("Error reading from socket: %s", e)
                    self._connected = False
                    self._available = False
                    raise ConnectionError(f"Error reading from socket: {e}")

            # Timeout reached, return what we have so far
            _LOGGER.warning("Socket read timed out, returning partial data")
            # Don't clear the buffer - save it for next read
            self._read_buffer = buffer
            return buffer

        except Exception as e:
            _LOGGER.error("Error in _socket_read_until: %s", e)
            self._connected = False
            self._available = False
            raise

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

    async def _background_connect(self):
        """Connect to the PDU in the background."""
        # If we're already connected, no need to reconnect
        if self._connected and self._socket is not None:
            return

        try:
            _LOGGER.debug("Attempting background connection to %s", self._host)
            await self.connect()

            if self._connected:
                _LOGGER.info("Connected to %s", self._host)

                # Make sure the command processor is running
                self._ensure_command_processor_running()

                # Load initial data in a separate task to not block
                asyncio.create_task(self._load_initial_data())
            else:
                _LOGGER.error("Failed to establish socket connection to %s", self._host)
                self._schedule_reconnect()

        except asyncio.CancelledError:
            # Task was cancelled - exit gracefully
            _LOGGER.debug("Background connection task was cancelled")
            return
        except Exception as e:
            _LOGGER.error("Error connecting to %s: %s", self._host, e)
            self._connected = False
            self._available = False
            self._schedule_reconnect()

    def _schedule_reconnect(self):
        """Schedule a reconnection with exponential backoff."""
        if not hasattr(self, "_reconnect_attempts"):
            self._reconnect_attempts = 0

        self._reconnect_attempts += 1

        # Calculate backoff delay with max of 5 minutes (300 seconds)
        delay = min(300, 2 ** min(self._reconnect_attempts, 8))

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
                        self._background_connect(), timeout=self._connection_timeout
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
        """Connect to the PDU."""
        try:
            if self._connected and self._socket:
                _LOGGER.debug("Already connected to %s", self._host)
                return True

            # Create socket connection
            self._socket = await self._create_socket_connection()

            if not self._socket:
                _LOGGER.error("Failed to create socket connection")
                return False

            # Login if needed
            if await self._login():
                self._connected = True
                self._available = True

                # Try to get device info
                await self.get_device_info()

                return True
            else:
                _LOGGER.error("Failed to login")
                await self._close_socket()
                return False

        except Exception as e:
            _LOGGER.error("Error connecting: %s", e)
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
            return self.pdu_info

        try:
            # Check if we need to get info (first run)
            if not self.pdu_model:
                _LOGGER.info("Getting PDU information")

                # Get system information
                system_info = await self.send_command("show system info")
                if system_info:
                    # Extract model information
                    model_match = re.search(r"Model Name:\s*([^\r\n]+)", system_info)
                    if model_match:
                        self.pdu_model = model_match.group(1).strip()
                        _LOGGER.debug("Found PDU model: %s", self.pdu_model)

                    # Extract firmware
                    firmware_match = re.search(
                        r"Firmware Version:\s*([^\r\n]+)", system_info
                    )
                    if firmware_match:
                        self.pdu_firmware = firmware_match.group(1).strip()
                        _LOGGER.debug("Found PDU firmware: %s", self.pdu_firmware)

                    # Extract serial number
                    serial_match = re.search(
                        r"Serial Number:\s*([^\r\n]+)", system_info
                    )
                    if serial_match:
                        self.pdu_serial = serial_match.group(1).strip()
                        _LOGGER.debug("Found PDU serial: %s", self.pdu_serial)

                    # If we don't have a serial number, generate one based on hostname
                    if not self.pdu_serial:
                        self.pdu_serial = f"RLNK_{self._host.replace('.', '_')}"
                        _LOGGER.debug("Generated PDU serial: %s", self.pdu_serial)

                    # Get outlet count
                    outlets_info = await self.send_command("show outlets")
                    if outlets_info:
                        # Count the number of outlets by counting the outlet rows
                        outlet_rows = re.findall(
                            r"^\s*\d+\s+\|", outlets_info, re.MULTILINE
                        )
                        if outlet_rows:
                            num_outlets = len(outlet_rows)
                            _LOGGER.info("Found %s outlets", num_outlets)

                # Update the pdu_info dictionary
                self.pdu_info = {
                    "model": self.pdu_model,
                    "firmware": self.pdu_firmware,
                    "serial": self.pdu_serial,
                    "name": self.pdu_name,
                }

            return self.pdu_info

        except Exception as e:
            _LOGGER.error("Error getting device info: %s", e)
            return self.pdu_info

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
        model = self.pdu_model

        if not model or model == "AUTO_DETECT" or model == "Racklink PDU":
            # Use controller response to determine outlet count
            outlet_count = (
                max(list(self.outlet_states.keys())) if self.outlet_states else 8
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
            if not self.pdu_model:
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
            self.outlet_states[outlet_num] = state == "on"
            data["state"] = state == "on"

        # Extract current (A)
        current_match = re.search(
            r"RMS Current:\s*([\d.]+)\s*A", response, re.IGNORECASE
        )
        if current_match:
            try:
                current = float(current_match.group(1))
                self.outlet_current[outlet_num] = current
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
                self.outlet_voltage[outlet_num] = voltage
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
                self.outlet_power[outlet_num] = power
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
                self.outlet_energy[outlet_num] = energy
                data["energy"] = energy
            except ValueError:
                _LOGGER.error("Failed to parse energy value: %s", energy_match.group(1))

        # Extract power factor
        pf_match = re.search(r"Power Factor:\s*([\d.]+)", response, re.IGNORECASE)
        if pf_match and pf_match.group(1) != "---":
            try:
                power_factor = float(pf_match.group(1))
                self.outlet_power_factor[outlet_num] = power_factor
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
                self.outlet_apparent_power[outlet_num] = apparent_power
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
                self.outlet_line_frequency[outlet_num] = frequency
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
                self.outlet_names[outlet_num] = outlet_name
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
            missing_power_data = [o for o in all_outlets if o not in self.outlet_power]

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
                self._read_buffer = b""  # Clear buffer on disconnect

    def _ensure_command_processor_running(self) -> None:
        """Ensure the command processor task is running."""
        if self._command_process_task is None or self._command_process_task.done():
            _LOGGER.debug("Starting command processor task")
            self._command_process_task = asyncio.create_task(
                self._process_command_queue()
            )
            # Add a done callback to log when the task completes
            self._command_process_task.add_done_callback(
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

            # Read initial welcome or prompt
            _LOGGER.debug("Reading initial prompt")
            try:
                initial_data = await self._socket_read_until(b">", timeout=5)
                _LOGGER.debug("Initial prompt: %s", initial_data)

                # If we're already at a prompt, we're good to go
                if initial_data.endswith(b">"):
                    _LOGGER.debug("Already at prompt, no need to login")
                    return True
            except Exception as e:
                _LOGGER.error("Error reading initial prompt: %s", e)
                return False

            # Try common login formats - most Racklink devices
            # don't require authentication but might have a welcome screen
            for attempt in range(3):
                try:
                    # Try sending a blank line
                    await self._socket_write(b"\r\n")
                    response = await self._socket_read_until(b">", timeout=5)

                    if b">" in response:
                        _LOGGER.debug("Successfully logged in")
                        return True
                except Exception as e:
                    _LOGGER.warning("Login attempt %s failed: %s", attempt, e)
                    # Small sleep between attempts
                    await asyncio.sleep(1)

            _LOGGER.error("Failed to login after multiple attempts")
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
        if self._command_process_task and not self._command_process_task.done():
            _LOGGER.debug("Cancelling command processor task")
            self._command_process_task.cancel()
            try:
                # Wait briefly for cancellation
                await asyncio.wait_for(
                    asyncio.shield(self._command_process_task), timeout=1
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
