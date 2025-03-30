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
        port: int = 23,
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

        # Socket connection
        self._socket = None
        self._reader = None
        self._writer = None
        self._connected = False
        self._available = False
        self._reconnect_tries = 0

        # Command queue
        self._command_queue = []
        self._processing_commands = False

        # Device info
        self.pdu_info = {
            "model": None,
            "firmware": None,
            "serial": None,
            "num_outlets": None,
        }

        # Data storage
        self.outlet_states = {}  # outlet_num -> bool
        self.outlet_names = {}  # outlet_num -> str
        self.outlet_current = {}  # outlet_num -> float (A)
        self.outlet_power = {}  # outlet_num -> float (W)
        self.outlet_energy = {}  # outlet_num -> float (Wh)
        self.outlet_voltage = {}  # outlet_num -> float (V)
        self.outlet_power_factor = {}  # outlet_num -> float (decimal)

        # Status tracking
        self._last_update = 0
        self._shutdown_requested = False

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
        try:
            await self._connect()
            if self._socket:
                self._connected = True
                _LOGGER.info("Connected to %s", self._host)
                self._reconnect_tries = 0
                await self._load_initial_data()
                await self._send_queued_commands()
        except Exception as e:
            _LOGGER.error("Error connecting to %s: %s", self._host, e)
            self._connected = False
            self._reconnect_tries += 1
            # Exponential backoff up to a max of 5 minutes
            delay = min(300, 2 ** min(self._reconnect_tries, 8))
            _LOGGER.info("Will retry in %s seconds", delay)
            # Schedule reconnection
            asyncio.create_task(self._delayed_reconnect(delay))

    async def _connect(self):
        """Connect to the PDU."""
        if self._socket and not self._socket.closed:
            _LOGGER.debug("Already connected to %s", self._host)
            return

        _LOGGER.debug("Connecting to %s:%s", self._host, self._port)
        try:
            # Clear the existing socket if any
            if self._socket and not self._socket.closed:
                self._socket.close()
                self._socket = None

            # Create a new socket connection
            self._socket = asyncio.open_connection(
                self._host,
                self._port,
                limit=65536,  # Increase buffer size for large responses
            )
            self._reader, self._writer = await asyncio.wait_for(
                self._socket, timeout=self._socket_timeout
            )
            self._connected = True

            # Read the welcome message
            welcome = await asyncio.wait_for(
                self._reader.read(4096), timeout=self._command_timeout
            )
            welcome_text = welcome.decode(errors="ignore")

            # Check if login prompt is present
            if "Username:" in welcome_text:
                _LOGGER.debug("Logging in with username: %s", self._username)
                self._writer.write(f"{self._username}\r\n".encode())
                await self._writer.drain()

                # Wait for password prompt
                auth1 = await asyncio.wait_for(
                    self._reader.read(1024), timeout=self._command_timeout
                )
                auth1_text = auth1.decode(errors="ignore")

                if "Password:" in auth1_text:
                    _LOGGER.debug("Sending password")
                    self._writer.write(f"{self._password}\r\n".encode())
                    await self._writer.drain()

                    # Wait for login confirmation
                    auth2 = await asyncio.wait_for(
                        self._reader.read(1024), timeout=self._command_timeout
                    )
                    auth2_text = auth2.decode(errors="ignore")

                    if "Login failed" in auth2_text:
                        raise ConnectionError("Login failed - incorrect credentials")

            # Set terminal width to avoid paging
            _LOGGER.debug("Setting terminal width to avoid paging")
            self._writer.write("term width 511\r\n".encode())
            await self._writer.drain()

            # Clear any pending output
            term_response = await asyncio.wait_for(
                self._reader.read(1024), timeout=self._command_timeout
            )

            _LOGGER.debug("Successfully connected to %s", self._host)
            self._available = True
            return True

        except asyncio.TimeoutError:
            _LOGGER.error("Connection to %s timed out", self._host)
            self._connected = False
            self._available = False
            if self._writer:
                self._writer.close()
                try:
                    await self._writer.wait_closed()
                except Exception:
                    pass
            self._reader = None
            self._writer = None
            raise

        except Exception as e:
            _LOGGER.error("Failed to connect to %s: %s", self._host, e)
            self._connected = False
            self._available = False
            if self._writer:
                self._writer.close()
                try:
                    await self._writer.wait_closed()
                except Exception:
                    pass
            self._reader = None
            self._writer = None
            raise

    async def _delayed_reconnect(self, delay):
        """Reconnect after a delay."""
        _LOGGER.debug("Scheduling reconnection in %s seconds", delay)
        try:
            await asyncio.sleep(delay)
            _LOGGER.debug("Attempting reconnection to %s", self._host)
            await self._background_connect()
        except Exception as e:
            _LOGGER.error("Error in delayed reconnection: %s", e)

    async def _load_initial_data(self):
        """Load initial data from the PDU."""
        _LOGGER.debug("Loading initial PDU data")
        try:
            # Get PDU model information
            await self._get_pdu_info()

            # Initialize outlet states
            await self.get_all_outlet_states()
        except Exception as e:
            _LOGGER.error("Error loading initial PDU data: %s", e)

    async def _get_pdu_info(self):
        """Get PDU model and system information."""
        _LOGGER.debug("Getting PDU model information")
        self.pdu_info = {
            "model": None,
            "firmware": None,
            "serial": None,
            "num_outlets": None,
        }

        # Get system information
        system_info = await self.send_command("show system")
        if system_info:
            # Extract model
            model_match = re.search(r"Model Name:\s*([^\r\n]+)", system_info)
            if model_match:
                self.pdu_info["model"] = model_match.group(1).strip()
                _LOGGER.info("PDU Model: %s", self.pdu_info["model"])

            # Extract firmware
            firmware_match = re.search(r"Firmware Version:\s*([^\r\n]+)", system_info)
            if firmware_match:
                self.pdu_info["firmware"] = firmware_match.group(1).strip()
                _LOGGER.info("PDU Firmware: %s", self.pdu_info["firmware"])

            # Extract serial number
            serial_match = re.search(r"Serial Number:\s*([^\r\n]+)", system_info)
            if serial_match:
                self.pdu_info["serial"] = serial_match.group(1).strip()
                _LOGGER.debug("PDU Serial: %s", self.pdu_info["serial"])

        # Get outlet count
        outlets_info = await self.send_command("show outlets")
        if outlets_info:
            # Count the number of outlets by counting the outlet rows
            outlet_rows = re.findall(r"^\s*\d+\s+\|", outlets_info, re.MULTILINE)
            if outlet_rows:
                self.pdu_info["num_outlets"] = len(outlet_rows)
                _LOGGER.info("PDU Outlets: %s", self.pdu_info["num_outlets"])

        return self.pdu_info

    def get_model_capabilities(self):
        """Get capabilities for the current PDU model."""
        # Use actual data if available
        if hasattr(self, "pdu_info") and self.pdu_info.get("num_outlets"):
            return {
                "num_outlets": self.pdu_info["num_outlets"],
                "model": self.pdu_info.get("model", "Unknown"),
                "has_current_sensing": "RLM" in str(self.pdu_info.get("model", "")),
            }

        # Fallback to known models
        if self._model in MODEL_CAPABILITIES:
            return MODEL_CAPABILITIES[self._model]
        # Default to RLNK-215 if model not specified or not found
        return MODEL_CAPABILITIES["RLNK-215"]

    async def _connect_only(self) -> None:
        """Connect to the device but skip loading initial status."""
        if self._shutdown_requested:
            _LOGGER.debug("Shutdown requested, not connecting to %s", self._host)
            return

        async with self._connection_lock:
            if self._connected:
                _LOGGER.debug(
                    "Already connected to %s, skipping connection", self._host
                )
                return

            _LOGGER.info(
                "Connecting to Middle Atlantic Racklink at %s:%s",
                self._host,
                self._port,
            )
            try:
                # Create socket connection with timeout
                self._socket = await asyncio.wait_for(
                    self._create_socket_connection(), timeout=self._socket_timeout
                )

                if not self._socket:
                    raise ConnectionError(
                        f"Failed to connect to {self._host}:{self._port}"
                    )

                await self.login()
                self._connected = True
                self._available = True
                self._last_error = None
                self._last_error_time = None
                self._reconnect_tries = 0
                self._read_buffer = b""  # Clear read buffer on new connection
                _LOGGER.info(
                    "Successfully connected to Middle Atlantic Racklink at %s (basic connection)",
                    self._host,
                )
            except (asyncio.TimeoutError, ConnectionError) as e:
                if self._socket:
                    # Close socket in a thread
                    def close_socket(sock):
                        try:
                            sock.close()
                        except Exception as close_err:
                            _LOGGER.debug("Error closing socket: %s", close_err)

                    await asyncio.to_thread(close_socket, self._socket)
                    self._socket = None
                self._connected = False
                self._handle_error(f"Connection failed: {e}")
                raise ValueError(f"Connection failed: {e}") from e
            except Exception as e:
                if self._socket:
                    # Close socket in a thread
                    def close_socket(sock):
                        try:
                            sock.close()
                        except Exception as close_err:
                            _LOGGER.debug("Error closing socket: %s", close_err)

                    await asyncio.to_thread(close_socket, self._socket)
                    self._socket = None
                self._connected = False
                self._handle_error(f"Unexpected error during connection: {e}")
                raise ValueError(f"Connection failed: {e}") from e

    async def login(self) -> None:
        """Login to the device."""
        try:
            # Wait for username prompt with timeout
            _LOGGER.debug("Waiting for Username prompt")
            response = await asyncio.wait_for(
                self._socket_read_until(b"Username:", self._connection_timeout),
                timeout=self._connection_timeout,
            )
            _LOGGER.debug("Got Username prompt, sending username")

            # Send username with CRLF
            await self._socket_write(f"{self._username}\r\n".encode())

            # Wait for password prompt with timeout
            _LOGGER.debug("Waiting for Password prompt")
            response = await asyncio.wait_for(
                self._socket_read_until(b"Password:", self._connection_timeout),
                timeout=self._connection_timeout,
            )
            _LOGGER.debug("Got Password prompt, sending password")

            # Send password with CRLF
            await self._socket_write(f"{self._password}\r\n".encode())

            # Wait for command prompt with timeout
            _LOGGER.debug("Waiting for command prompt")
            response = await asyncio.wait_for(
                self._socket_read_until(b"#", self._connection_timeout),
                timeout=self._connection_timeout,
            )
            _LOGGER.debug("Got command prompt")

            if b"#" not in response:
                _LOGGER.error("Did not get command prompt after login")
                raise ValueError("Login failed: Invalid credentials")

            # Add a short delay after login to ensure device is ready for commands
            await asyncio.sleep(1)

        except asyncio.TimeoutError as exc:
            _LOGGER.error("Login timed out - no response from device")
            raise ConnectionError("Login timed out") from exc
        except ConnectionError as e:
            _LOGGER.error("Connection error during login: %s", e)
            raise ConnectionError(f"Login failed: {e}") from e
        except Exception as e:
            _LOGGER.error("Unexpected error during login: %s", e)
            self._handle_error(f"Login failed: {e}")
            raise ValueError(f"Login failed: {e}") from e

    async def connect(self) -> None:
        """Connect to the RackLink device."""
        # Don't connect if shutdown was requested
        if self._shutdown_requested:
            _LOGGER.debug("Shutdown requested, not connecting to %s", self._host)
            return

        async with self._connection_lock:
            if self._connected and self._socket is not None:
                _LOGGER.debug(
                    "Already connected to %s, skipping connection", self._host
                )
                return

            # Clean up any existing broken connection
            if self._socket is not None:
                _LOGGER.debug("Cleaning up existing broken socket connection")
                try:
                    # Close socket in a thread
                    def close_socket(sock):
                        try:
                            sock.close()
                        except Exception as close_err:
                            _LOGGER.debug("Error closing socket: %s", close_err)

                    await asyncio.to_thread(close_socket, self._socket)
                except Exception as e:
                    _LOGGER.debug("Error closing existing socket connection: %s", e)
                finally:
                    self._socket = None
                    self._connected = False

            _LOGGER.info(
                "Connecting to Middle Atlantic Racklink at %s:%s",
                self._host,
                self._port,
            )
            try:
                # Create socket connection with timeout
                self._socket = await asyncio.wait_for(
                    self._create_socket_connection(), timeout=self._connection_timeout
                )

                if not self._socket:
                    raise ConnectionError(
                        f"Failed to connect to {self._host}:{self._port}"
                    )

                # Login with timeout
                await asyncio.wait_for(self.login(), timeout=self._connection_timeout)

                self._connected = True
                self._available = True
                self._last_error = None
                self._last_error_time = None
                self._reconnect_tries = 0
                self._read_buffer = b""  # Clear read buffer on new connection

                # Start command queue processing task if not running
                if (
                    self._command_processor_task is None
                    or self._command_processor_task.done()
                ):
                    _LOGGER.debug("Starting command processor task")
                    self._command_processor_task = asyncio.create_task(
                        self._process_command_queue()
                    )

                _LOGGER.info(
                    "Successfully connected to Middle Atlantic Racklink at %s",
                    self._host,
                )
            except (asyncio.TimeoutError, ConnectionError) as e:
                if self._socket:
                    try:
                        # Close socket in a thread
                        def close_socket(sock):
                            try:
                                sock.close()
                            except Exception as close_err:
                                _LOGGER.debug("Error closing socket: %s", close_err)

                        await asyncio.to_thread(close_socket, self._socket)
                    except Exception as close_err:
                        _LOGGER.debug(
                            "Error closing socket during connection failure: %s",
                            close_err,
                        )
                    self._socket = None
                self._connected = False
                self._available = False
                self._handle_error(f"Connection failed: {e}")
                raise ValueError(f"Connection failed: {e}") from e
            except Exception as e:
                if self._socket:
                    try:
                        # Close socket in a thread
                        def close_socket(sock):
                            try:
                                sock.close()
                            except Exception as close_err:
                                _LOGGER.debug("Error closing socket: %s", close_err)

                        await asyncio.to_thread(close_socket, self._socket)
                    except Exception as close_err:
                        _LOGGER.debug(
                            "Error closing socket during connection failure: %s",
                            close_err,
                        )
                    self._socket = None
                self._connected = False
                self._available = False
                self._handle_error(f"Unexpected error during connection: {e}")
                raise ValueError(f"Connection failed: {e}") from e

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        async with self._connection_lock:
            _LOGGER.debug("Disconnecting from %s", self.host)

            # Cancel command processor task if running
            if self._command_processor_task and not self._command_processor_task.done():
                _LOGGER.debug("Cancelling command processor task")
                try:
                    self._command_processor_task.cancel()
                    # Wait briefly for task to cancel
                    try:
                        await asyncio.wait_for(self._command_processor_task, timeout=1)
                    except asyncio.TimeoutError:
                        # Task didn't cancel in time, can proceed anyway
                        pass
                    except asyncio.CancelledError:
                        # Task was successfully cancelled
                        pass
                    except Exception as e:
                        _LOGGER.debug(
                            "Error waiting for processor task cancellation: %s", e
                        )
                except Exception as e:
                    _LOGGER.debug("Error cancelling command processor task: %s", e)

            # Close socket connection
            if self.socket:
                try:
                    # Send terminal exit command if still connected
                    if self._connected:
                        try:
                            _LOGGER.debug("Sending exit command")
                            await asyncio.wait_for(
                                self._socket_write("exit\r\n".encode()), timeout=2
                            )
                        except Exception as e:
                            _LOGGER.debug("Error sending exit command: %s", e)

                    # Close the connection
                    _LOGGER.debug("Closing socket connection")

                    # Close socket in a thread
                    def close_socket(sock):
                        try:
                            sock.close()
                        except Exception as close_err:
                            _LOGGER.debug("Error closing socket: %s", close_err)

                    await asyncio.to_thread(close_socket, self.socket)
                except Exception as e:
                    _LOGGER.debug("Error during socket disconnect: %s", e)
                finally:
                    self.socket = None
                    self._read_buffer = b""  # Clear buffer on disconnect

            self._connected = False
            _LOGGER.debug("Disconnected from %s", self.host)

    async def reconnect(self) -> None:
        """Attempt to reconnect to the device."""
        # First check if we're already shut down
        if self._shutdown_requested:
            _LOGGER.debug("Shutdown requested, not reconnecting")
            return

        # Prevent concurrent reconnections
        async with self._connection_lock:
            # Close and clean up any existing connection
            if self.socket is not None:
                _LOGGER.debug("Closing existing socket connection before reconnecting")
                try:
                    # Close socket in a thread
                    def close_socket(sock):
                        try:
                            sock.close()
                        except Exception as close_err:
                            _LOGGER.debug("Error closing socket: %s", close_err)

                    await asyncio.to_thread(close_socket, self.socket)
                except Exception as e:
                    _LOGGER.debug("Error closing socket connection: %s", e)
                finally:
                    self.socket = None
                    self._connected = False
                    self._read_buffer = b""  # Clear buffer on reconnect

            # Rate limit reconnection attempts
            if self._reconnect_attempts >= self._max_reconnect_attempts:
                _LOGGER.warning(
                    "Maximum reconnection attempts (%s) reached for %s, will not try again until next update cycle",
                    self._max_reconnect_attempts,
                    self.host,
                )
                return

            self._reconnect_attempts += 1
            backoff = min(2**self._reconnect_attempts, 300)  # Max 5 minutes
            _LOGGER.info(
                "Attempting to reconnect to %s (attempt %s/%s) in %s seconds...",
                self.host,
                self._reconnect_attempts,
                self._max_reconnect_attempts,
                backoff,
            )

            await asyncio.sleep(backoff)

            try:
                # Create fresh socket connection
                _LOGGER.debug("Creating new socket connection during reconnect")
                self.socket = await asyncio.wait_for(
                    self._create_socket_connection(), timeout=self._socket_timeout
                )

                if not self.socket:
                    _LOGGER.error("Failed to create socket connection during reconnect")
                    return

                await asyncio.wait_for(self.login(), timeout=self._socket_timeout)
                self._connected = True
                self._available = True
                _LOGGER.info("Successfully reconnected to %s", self.host)
            except (asyncio.TimeoutError, Exception) as e:
                _LOGGER.error("Failed to reconnect to %s: %s", self.host, e)
                if self.socket:
                    try:
                        # Close socket in a thread
                        def close_socket(sock):
                            try:
                                sock.close()
                            except Exception as close_err:
                                _LOGGER.debug("Error closing socket: %s", close_err)

                        await asyncio.to_thread(close_socket, self.socket)
                    except Exception as close_err:
                        _LOGGER.debug(
                            "Error closing socket during reconnect failure: %s",
                            close_err,
                        )
                    self.socket = None
                self._connected = False
                self._available = False

    async def send_command(self, command):
        """Send a command to the PDU and return the response."""
        if not self._connected:
            _LOGGER.warning("Cannot send command, not connected: %s", command)
            # Queue commands if not connected
            self._command_queue.append(command)
            _LOGGER.debug("Queued command for later: %s", command)
            return None

        if not command:
            _LOGGER.warning("Attempted to send empty command")
            return None

        # Add command to queue to ensure serialized execution
        future = asyncio.Future()
        self._command_queue.append((command, future))

        # Process the command queue if we're not already processing
        if not self._processing_commands:
            asyncio.create_task(self._send_queued_commands())

        try:
            # Wait for the command to be processed with a timeout
            return await asyncio.wait_for(future, timeout=self._command_timeout)
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout waiting for command response: %s", command)
            return None
        except Exception as e:
            _LOGGER.error("Error waiting for command response: %s - %s", command, e)
            return None

    async def _send_queued_commands(self):
        """Process queued commands one at a time."""
        if self._processing_commands:
            return

        self._processing_commands = True

        try:
            while self._command_queue and self._connected:
                item = self._command_queue.pop(0)

                # Handle simple string commands (no future)
                if isinstance(item, str):
                    command = item
                    _LOGGER.debug("Processing queued command (no future): %s", command)
                    try:
                        await self._send_command_direct(command)
                    except Exception as e:
                        _LOGGER.error(
                            "Error sending queued command: %s - %s", command, e
                        )
                    continue

                # Handle command with future
                command, future = item

                _LOGGER.debug("Processing queued command: %s", command)
                try:
                    response = await self._send_command_direct(command)
                    if not future.done():
                        future.set_result(response)
                except Exception as e:
                    _LOGGER.error("Error sending command: %s - %s", command, e)
                    if not future.done():
                        future.set_exception(e)

                # Small delay between commands to avoid overwhelming the device
                await asyncio.sleep(0.1)

        except Exception as e:
            _LOGGER.error("Error processing command queue: %s", e)
        finally:
            self._processing_commands = False

            # If there are still commands in the queue, process them
            if self._command_queue and self._connected:
                asyncio.create_task(self._send_queued_commands())

    async def _send_command_direct(self, command):
        """Send a command directly to the PDU and get the response."""
        if not self._connected or not self._writer or not self._reader:
            _LOGGER.warning("Cannot send command, connection not ready: %s", command)
            raise ConnectionError("Not connected")

        try:
            # Send the command
            _LOGGER.debug("Sending command: %s", command)
            self._writer.write(f"{command}\r\n".encode())
            await self._writer.drain()

            # Read the response with a timeout
            response_bytes = await asyncio.wait_for(
                self._read_response(), timeout=self._command_timeout
            )

            # Decode the response
            response = response_bytes.decode(errors="ignore")

            # Check for error messages
            if "error" in response.lower() or "invalid" in response.lower():
                _LOGGER.warning(
                    "Command error: %s - Response: %s", command, response.strip()
                )

            # Check if we got a prompt at the end (command completed)
            if not response.strip().endswith(("#", ">")):
                _LOGGER.debug("Response doesn't end with prompt, may be incomplete")

            return response

        except asyncio.TimeoutError:
            _LOGGER.error("Timeout reading response for command: %s", command)
            # Don't close connection on timeout, just return None
            raise TimeoutError(f"Command timed out: {command}")

        except Exception as e:
            _LOGGER.error("Error sending command: %s - %s", command, e)
            # If connection seems broken, mark as disconnected
            if isinstance(e, (ConnectionError, OSError)):
                _LOGGER.warning("Connection seems broken, marking as disconnected")
                self._connected = False
                # Close the connection
                if self._writer:
                    try:
                        self._writer.close()
                        await self._writer.wait_closed()
                    except Exception:
                        pass
                self._reader = None
                self._writer = None
                # Schedule reconnection
                asyncio.create_task(self._delayed_reconnect(5))
            raise

    async def _read_response(self):
        """Read response from the device until prompt or timeout."""
        buffer = b""
        start_time = time.time()
        prompt_patterns = [b"#", b">"]

        while time.time() - start_time < self._command_timeout:
            try:
                # Try to read a chunk
                chunk = await asyncio.wait_for(
                    self._reader.read(4096), timeout=1.0  # Short timeout for each read
                )

                if not chunk:
                    # Connection closed
                    _LOGGER.warning("Connection closed while reading response")
                    self._connected = False
                    break

                buffer += chunk

                # Check if we've reached a prompt (end of response)
                if any(buffer.rstrip().endswith(prompt) for prompt in prompt_patterns):
                    break

                # Continue reading if we haven't reached a prompt yet

            except asyncio.TimeoutError:
                # No more data available within timeout
                if buffer:
                    # We have some data, so return it
                    break
                # Otherwise, continue waiting for more data

            except Exception as e:
                _LOGGER.error("Error reading response: %s", e)
                if buffer:
                    # Return what we have so far
                    break
                raise

        return buffer

    async def shutdown(self) -> None:
        """Shut down the controller and release resources."""
        _LOGGER.info("Shutting down controller for %s", self.host)
        self._shutdown_requested = True

        # Cancel all pending command futures
        while not self._command_queue.empty():
            try:
                cmd, future = self._command_queue.get_nowait()
                if not future.done():
                    future.cancel()
                self._command_queue.task_done()
            except Exception:
                break

        # Cancel the command processor task if running
        if self._command_processor_task and not self._command_processor_task.done():
            _LOGGER.debug("Cancelling command processor task")
            try:
                self._command_processor_task.cancel()
                try:
                    # Wait briefly for task to cancel
                    await asyncio.wait_for(self._command_processor_task, timeout=1)
                except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                    pass
            except Exception as e:
                _LOGGER.debug("Error cancelling command processor: %s", e)

        # Disconnect from device
        await self.disconnect()
        _LOGGER.info("Controller for %s has been shut down", self.host)

    async def get_initial_status(self):
        """Get initial device status."""
        # This method is kept for backwards compatibility but now uses the non-blocking approach
        asyncio.create_task(self._load_initial_data())
        self._last_update = asyncio.get_event_loop().time()

    async def get_pdu_details(self):
        """Get PDU details including name, firmware, and serial number."""
        _LOGGER.debug("Getting PDU details")
        # Use a simplified fallback approach for PDU details
        try:
            # First try the standard command
            response = await self.send_command("show pdu details")

            # If that fails, try alternative commands
            if not response:
                _LOGGER.debug(
                    "Initial PDU details command failed, trying alternative: show device"
                )
                response = await self.send_command("show device")

            # If that still fails, try another alternative
            if not response:
                _LOGGER.debug(
                    "Second PDU details command failed, trying alternative: show identity"
                )
                response = await self.send_command("show identity")

            if not response:
                _LOGGER.error("No response when getting PDU details")
                # Set basic fallback values to prevent further errors
                if not self.pdu_name or self.pdu_name == f"Racklink PDU ({self.host})":
                    self.pdu_name = f"Racklink PDU ({self.host})"
                if not self.pdu_serial or self.pdu_serial == f"{self.host}_{self.port}":
                    self.pdu_serial = f"{self.host}_{self.port}"
                if not self.pdu_model or self.pdu_model == "Racklink PDU":
                    self.pdu_model = "Racklink PDU"
                self.pdu_firmware = self.pdu_firmware or "Unknown"
                return

            _LOGGER.debug(
                "PDU details response: %s", response[:200].replace("\r\n", " ")
            )

            try:
                # Try to extract data using regex patterns from the response
                name_match = re.search(r"'(.+)'", response)
                if name_match:
                    self.pdu_name = name_match.group(1)
                    _LOGGER.debug("Found PDU name: %s", self.pdu_name)

                fw_match = re.search(r"Firmware Version: (.+)", response)
                if fw_match:
                    self.pdu_firmware = fw_match.group(1).strip()
                    _LOGGER.debug("Found PDU firmware: %s", self.pdu_firmware)

                sn_match = re.search(r"Serial Number: (.+)", response)
                if sn_match:
                    self.pdu_serial = sn_match.group(1).strip()
                    _LOGGER.debug("Found PDU serial: %s", self.pdu_serial)

                # Handle model detection or use specified model
                model_match = re.search(r"Model: (.+)", response)
                detected_model = ""
                if model_match:
                    detected_model = model_match.group(1).strip()
                    _LOGGER.debug("Found PDU model: %s", detected_model)

                    # If auto-detect, use the detected model
                    if self.specified_model == "AUTO_DETECT":
                        self.pdu_model = detected_model
                    else:
                        # User specified a model, check if it matches detected model
                        if self.specified_model in detected_model:
                            # Model matches detected, use specific model for better handling
                            self.pdu_model = self.specified_model
                            _LOGGER.debug("Using specified model: %s", self.pdu_model)
                        else:
                            # Model doesn't match - log warning but use user's preference
                            _LOGGER.warning(
                                "Specified model %s doesn't match detected model %s. Using specified model.",
                                self.specified_model,
                                detected_model,
                            )
                            self.pdu_model = self.specified_model
                else:
                    # No model detected, use specified model if not auto-detect
                    if self.specified_model != "AUTO_DETECT":
                        self.pdu_model = self.specified_model
                        _LOGGER.debug(
                            "No model detected, using specified model: %s",
                            self.pdu_model,
                        )
                    else:
                        _LOGGER.warning("Could not detect model and no model specified")

            except AttributeError as e:
                _LOGGER.error("Failed to parse PDU details: %s", e)
                _LOGGER.debug("Response causing parse failure: %s", response[:200])
                self._handle_error(f"Failed to parse PDU details: {e}")

            # Get MAC address
            _LOGGER.debug("Getting network interface details")
            response = await self.send_command("show network interface eth1")
            if not response:
                _LOGGER.error("No response when getting network interface details")
                return

            _LOGGER.debug(
                "Network interface response: %s", response[:200].replace("\r\n", " ")
            )

            try:
                mac_match = re.search(r"MAC address: (.+)", response)
                if mac_match:
                    self.pdu_mac = mac_match.group(1).strip()
                    _LOGGER.debug("Found PDU MAC: %s", self.pdu_mac)
            except AttributeError as e:
                _LOGGER.error("Failed to parse MAC address: %s", e)
                self._handle_error(f"Failed to parse MAC address: {e}")

        except Exception as e:
            _LOGGER.error("Error getting PDU details: %s", e)
            # Set basic fallback values to prevent further errors
            if not self.pdu_name or self.pdu_name == f"Racklink PDU ({self.host})":
                self.pdu_name = f"Racklink PDU ({self.host})"
            if not self.pdu_serial or self.pdu_serial == f"{self.host}_{self.port}":
                self.pdu_serial = f"{self.host}_{self.port}"
            if not self.pdu_model or self.pdu_model == "Racklink PDU":
                self.pdu_model = "Racklink PDU"
            self.pdu_firmware = self.pdu_firmware or "Unknown"

    async def get_all_outlet_states(self):
        """Get all outlet states."""
        _LOGGER.debug("Getting states for all outlets")

        if not self._connected:
            _LOGGER.warning("Not connected, cannot get outlet states")
            return False

        try:
            # Use the number of outlets from the detected or specified model
            capabilities = self.get_model_capabilities()
            num_outlets = capabilities.get(
                "num_outlets", 8
            )  # Default to 8 if not specified
            has_current = capabilities.get("has_current_sensing", False)

            success_count = 0

            for outlet_num in range(1, num_outlets + 1):
                # Get detailed info for this outlet
                cmd = f"show outlets {outlet_num} details"
                response = await self.send_command(cmd)

                if not response:
                    _LOGGER.warning("No response for outlet %d details", outlet_num)
                    continue

                # Parse power state
                state_match = re.search(
                    r"Power state:\s*(\w+)", response, re.IGNORECASE
                )
                if state_match:
                    raw_state = state_match.group(1)
                    is_on = raw_state.lower() in ["on", "1", "true", "yes", "active"]
                    self.outlet_states[outlet_num] = is_on
                    success_count += 1
                    _LOGGER.debug(
                        "Outlet %d state: %s (raw: %s)",
                        outlet_num,
                        "ON" if is_on else "OFF",
                        raw_state,
                    )
                else:
                    _LOGGER.warning(
                        "No power state found in response for outlet %d: %s",
                        outlet_num,
                        response[:100].replace("\r\n", " "),
                    )

                # Parse name if available
                name_match = re.search(r"Name:\s*([^\r\n]+)", response, re.IGNORECASE)
                if name_match and name_match.group(1).strip():
                    name = name_match.group(1).strip()
                    self.outlet_names[outlet_num] = name
                    _LOGGER.debug("Outlet %d name: %s", outlet_num, name)
                else:
                    # Set default name if not found
                    if outlet_num not in self.outlet_names:
                        self.outlet_names[outlet_num] = f"Outlet {outlet_num}"

                # If this model has current sensing, parse current and power
                if has_current:
                    # Extract current
                    current_match = re.search(
                        r"RMS Current:\s*([\d.]+)\s*A", response, re.IGNORECASE
                    )
                    if current_match:
                        try:
                            current = float(current_match.group(1).strip())
                            self.outlet_current[outlet_num] = current
                            _LOGGER.debug(
                                "Outlet %d current: %.2f A", outlet_num, current
                            )
                        except (ValueError, TypeError):
                            _LOGGER.warning(
                                "Invalid current value for outlet %d", outlet_num
                            )

                    # Extract power
                    power_match = re.search(
                        r"Active Power:\s*([\d.]+)\s*W", response, re.IGNORECASE
                    )
                    if power_match:
                        try:
                            power = float(power_match.group(1).strip())
                            self.outlet_power[outlet_num] = power
                            _LOGGER.debug("Outlet %d power: %.2f W", outlet_num, power)
                        except (ValueError, TypeError):
                            _LOGGER.warning(
                                "Invalid power value for outlet %d", outlet_num
                            )

                # Brief delay to avoid overwhelming the device
                await asyncio.sleep(0.2)

            _LOGGER.info(
                "Successfully retrieved %d of %d outlet states",
                success_count,
                num_outlets,
            )
            return success_count > 0

        except Exception as e:
            _LOGGER.error("Error getting all outlet states: %s", e)
            return False

    async def set_outlet_state(self, outlet: int, state: bool):
        """Set an outlet's power state."""
        cmd = f"power outlets {outlet} {'on' if state else 'off'} /y"
        _LOGGER.debug(
            "Setting outlet %d to state: %s", outlet, "ON" if state else "OFF"
        )

        response = await self.send_command(cmd)
        if not response:
            _LOGGER.error(
                "No response when setting outlet %d state to %s", outlet, state
            )
            return False

        # Check if the command was acknowledged
        success_patterns = [
            r"outlet.*powered\s+on",  # For powering on
            r"outlet.*powered\s+off",  # For powering off
            r"Success",  # Generic success message
            r"OK",  # Generic success message
        ]

        command_successful = False
        for pattern in success_patterns:
            if re.search(pattern, response, re.IGNORECASE):
                command_successful = True
                break

        # Add a delay and then check the actual state of the outlet to verify change
        await asyncio.sleep(2)  # Wait for device to apply the change

        # Get the current state to verify
        outlet_cmd = f"show outlets {outlet} details"
        verify_response = await self.send_command(outlet_cmd)

        if verify_response:
            state_match = re.search(
                r"Power state:\s*(\w+)", verify_response, re.IGNORECASE
            )
            if state_match:
                current_state = state_match.group(1)
                is_on = current_state.lower() in ["on", "1", "true", "yes", "active"]

                if is_on == state:
                    _LOGGER.debug(
                        "Verified outlet %d state is now %s",
                        outlet,
                        "ON" if state else "OFF",
                    )
                    self.outlet_states[outlet] = state
                    return True
                else:
                    _LOGGER.warning(
                        "Outlet %d state verification failed: expected %s but got %s",
                        outlet,
                        "ON" if state else "OFF",
                        "ON" if is_on else "OFF",
                    )
                    return False

        if command_successful:
            _LOGGER.debug(
                "Successfully set outlet %d to %s", outlet, "ON" if state else "OFF"
            )
            # Update our local state cache
            self.outlet_states[outlet] = state
            return True
        else:
            # Command might have failed
            _LOGGER.warning(
                "Uncertain if outlet %d state change to %s was successful: %s",
                outlet,
                "ON" if state else "OFF",
                response[:100].replace("\r\n", " "),
            )
            return False

    async def cycle_outlet(self, outlet: int):
        """Cycle an outlet's power."""
        _LOGGER.debug("Cycling outlet %d", outlet)
        cmd = f"power outlets {outlet} cycle /y"
        response = await self.send_command(cmd)

        if not response:
            _LOGGER.error("No response when cycling outlet %d", outlet)
            return False

        # Check if the command was acknowledged
        success_patterns = [
            r"outlet.*cycling",  # Cycling confirmation
            r"Success",  # Generic success message
            r"OK",  # Generic success message
        ]

        command_successful = False
        for pattern in success_patterns:
            if re.search(pattern, response, re.IGNORECASE):
                command_successful = True
                break

        if not command_successful:
            _LOGGER.warning(
                "Uncertain if outlet %d cycle was initiated: %s",
                outlet,
                response[:100].replace("\r\n", " "),
            )

        # Wait for the cycle to complete (typical cycle time is 5 seconds)
        _LOGGER.debug("Waiting for outlet %d cycle to complete", outlet)
        await asyncio.sleep(5)

        # Verify outlet state after cycling (should be ON)
        try:
            cmd = f"show outlets {outlet} details"
            verify_response = await self.send_command(cmd)

            if verify_response:
                state_match = re.search(
                    r"Power state:\s*(\w+)", verify_response, re.IGNORECASE
                )
                if state_match:
                    current_state = state_match.group(1)
                    is_on = current_state.lower() in [
                        "on",
                        "1",
                        "true",
                        "yes",
                        "active",
                    ]

                    # Update our stored state
                    self.outlet_states[outlet] = is_on

                    _LOGGER.debug(
                        "After cycling, outlet %d state is: %s",
                        outlet,
                        "ON" if is_on else "OFF",
                    )

                    # Cycling should result in the outlet being ON
                    if not is_on:
                        _LOGGER.warning(
                            "Outlet %d is OFF after cycling - expected ON", outlet
                        )
                        return False

                    return True
                else:
                    _LOGGER.warning(
                        "Could not determine outlet %d state after cycling", outlet
                    )
            else:
                _LOGGER.warning(
                    "No response when verifying outlet %d state after cycling", outlet
                )
        except Exception as e:
            _LOGGER.error(
                "Error verifying outlet %d state after cycling: %s", outlet, e
            )

        # Return the success of the command if we couldn't verify
        return command_successful

    async def set_outlet_name(self, outlet: int, name: str):
        """Set an outlet's name."""
        cmd = "config"
        await self.send_command(cmd)
        cmd = f'outlet {outlet} name "{name}"'
        await self.send_command(cmd)
        cmd = "apply"
        await self.send_command(cmd)
        self.outlet_names[outlet] = name

    async def set_all_outlets(self, state: bool):
        """Set all outlets to the same state."""
        cmd = f"power outlets all {'on' if state else 'off'} /y"
        await self.send_command(cmd)
        for outlet in self.outlet_states:
            self.outlet_states[outlet] = state

    async def cycle_all_outlets(self):
        """Cycle all outlets."""
        _LOGGER.debug("Cycling all outlets")
        cmd = "power outlets all cycle /y"
        response = await self.send_command(cmd)

        if not response:
            _LOGGER.error("No response when cycling all outlets")
            return False

        # Check if the command was acknowledged
        success_patterns = [
            r"outlet.*cycling",  # Cycling confirmation
            r"Success",  # Generic success message
            r"OK",  # Generic success message
        ]

        command_successful = False
        for pattern in success_patterns:
            if re.search(pattern, response, re.IGNORECASE):
                command_successful = True
                break

        if not command_successful:
            _LOGGER.warning(
                "Uncertain if all outlets cycle was initiated: %s",
                response[:100].replace("\r\n", " "),
            )

        # Wait for the cycle to complete (typical cycle time is 5 seconds)
        _LOGGER.debug("Waiting for all outlets cycle to complete")
        await asyncio.sleep(7)  # Slight longer wait for all outlets

        # Verify outlet states after cycling (should be ON)
        try:
            # Use the number of outlets from the detected or specified model
            capabilities = self.get_model_capabilities()
            num_outlets = capabilities.get(
                "num_outlets", 8
            )  # Default to 8 if not specified

            all_on = True
            # Check a few outlets as a sample (checking all would be too much traffic)
            sample_outlets = [1, num_outlets]  # First and last outlet
            if num_outlets > 4:
                sample_outlets.append(
                    num_outlets // 2
                )  # Add middle outlet if there are many

            for outlet in sample_outlets:
                cmd = f"show outlets {outlet} details"
                verify_response = await self.send_command(cmd)

                if verify_response:
                    state_match = re.search(
                        r"Power state:\s*(\w+)", verify_response, re.IGNORECASE
                    )
                    if state_match:
                        current_state = state_match.group(1)
                        is_on = current_state.lower() in [
                            "on",
                            "1",
                            "true",
                            "yes",
                            "active",
                        ]

                        # Update our stored state
                        self.outlet_states[outlet] = is_on

                        _LOGGER.debug(
                            "After cycling all, outlet %d state is: %s",
                            outlet,
                            "ON" if is_on else "OFF",
                        )

                        # Cycling should result in the outlet being ON
                        if not is_on:
                            _LOGGER.warning(
                                "Outlet %d is OFF after cycling all - expected ON",
                                outlet,
                            )
                            all_on = False
                    else:
                        _LOGGER.warning(
                            "Could not determine outlet %d state after cycling all",
                            outlet,
                        )
                        all_on = False
                else:
                    _LOGGER.warning(
                        "No response when verifying outlet %d state after cycling all",
                        outlet,
                    )
                    all_on = False

                # Brief delay before checking next outlet
                await asyncio.sleep(0.2)

            # Set remaining outlet states to ON (optimistic)
            for outlet in range(1, num_outlets + 1):
                if outlet not in sample_outlets:
                    self.outlet_states[outlet] = True

            return all_on and command_successful

        except Exception as e:
            _LOGGER.error("Error verifying outlet states after cycling all: %s", e)

        # Return the success of the command if we couldn't verify
        return command_successful

    async def set_pdu_name(self, name: str):
        """Set the PDU name."""
        cmd = "config"
        await self.send_command(cmd)
        cmd = f'pdu name "{name}"'
        await self.send_command(cmd)
        cmd = "apply"
        await self.send_command(cmd)
        self.pdu_name = name

    async def get_surge_protection_status(self) -> bool:
        """Get surge protection status."""
        response = await self.send_command("show pdu details")
        match = re.search(r"Surge Protection: (\w+)", response)
        if match:
            return match.group(1) == "Active"
        return False

    async def get_sensor_values(self, force_refresh=False):
        """Get all sensor values."""
        _LOGGER.debug(
            "Getting sensor values%s", " (forced refresh)" if force_refresh else ""
        )
        try:
            response = await self.send_command("show inlets all details")
            if not response:
                _LOGGER.error("No response when getting sensor values")
                # Maintain existing sensor values
                return

            _LOGGER.debug(
                "Sensor values response: %s", response[:200].replace("\r\n", " ")
            )

            # Check if we actually got data
            if "RMS Voltage" not in response:
                _LOGGER.warning("Response doesn't contain expected inlet data")
                return

            # Extract voltage - using more flexible patterns with whitespace handling
            voltage_match = re.search(
                r"RMS Voltage:\s*([\d.]+)\s*V", response, re.IGNORECASE
            )
            if voltage_match:
                try:
                    self.sensors["voltage"] = float(voltage_match.group(1).strip())
                    _LOGGER.debug("Found voltage: %.2f V", self.sensors["voltage"])
                except ValueError:
                    _LOGGER.warning("Invalid voltage value")
            else:
                _LOGGER.debug("No voltage found in response")

            # Extract current
            current_match = re.search(
                r"RMS Current:\s*([\d.]+)\s*A", response, re.IGNORECASE
            )
            if current_match:
                try:
                    self.sensors["current"] = float(current_match.group(1).strip())
                    _LOGGER.debug("Found current: %.2f A", self.sensors["current"])
                except ValueError:
                    _LOGGER.warning("Invalid current value")
            else:
                _LOGGER.debug("No current found in response")

            # Extract power
            power_match = re.search(
                r"Active Power:\s*([\d.]+)\s*W", response, re.IGNORECASE
            )
            if power_match:
                try:
                    self.sensors["power"] = float(power_match.group(1).strip())
                    _LOGGER.debug("Found power: %.2f W", self.sensors["power"])
                except ValueError:
                    _LOGGER.warning("Invalid power value")
            else:
                _LOGGER.debug("No power found in response")

            # Extract energy - many devices also report total energy consumption
            energy_match = re.search(
                r"Active Energy:\s*([\d.]+)\s*Wh", response, re.IGNORECASE
            )
            if energy_match:
                try:
                    self.sensors["energy"] = float(energy_match.group(1).strip())
                    _LOGGER.debug("Found energy: %.2f Wh", self.sensors["energy"])
                except ValueError:
                    _LOGGER.warning("Invalid energy value")
            else:
                _LOGGER.debug("No energy found in response")

            # Extract frequency
            freq_match = re.search(
                r"Frequency:\s*([\d.]+)\s*Hz", response, re.IGNORECASE
            )
            if freq_match:
                try:
                    self.sensors["frequency"] = float(freq_match.group(1).strip())
                    _LOGGER.debug("Found frequency: %.2f Hz", self.sensors["frequency"])
                except ValueError:
                    _LOGGER.warning("Invalid frequency value")
            else:
                _LOGGER.debug("No frequency found in response")

            # Extract power factor
            pf_match = re.search(
                r"Power Factor:\s*([\d.]+)\s*%", response, re.IGNORECASE
            )
            if pf_match:
                try:
                    # Convert percentage to decimal (0-1)
                    power_factor = float(pf_match.group(1).strip()) / 100.0
                    self.sensors["power_factor"] = power_factor
                    _LOGGER.debug(
                        "Found power factor: %.2f", self.sensors["power_factor"]
                    )
                except ValueError:
                    _LOGGER.warning("Invalid power factor value")
            else:
                _LOGGER.debug("No power factor found in response")

            # Extract temperature
            temp_match = re.search(
                r"Temperature:\s*([\d.]+)\s*°C", response, re.IGNORECASE
            )
            if temp_match:
                try:
                    self.sensors["temperature"] = float(temp_match.group(1).strip())
                    _LOGGER.debug(
                        "Found temperature: %.1f °C", self.sensors["temperature"]
                    )
                except ValueError:
                    _LOGGER.warning("Invalid temperature value")
            else:
                _LOGGER.debug("No temperature found in response")
                # Some devices don't have temperature sensors
                self.sensors["temperature"] = None

        except Exception as e:
            _LOGGER.error("Failed to get sensor values: %s", e)
            # Don't clear sensor values on error - maintain last known good values
            return

    async def get_all_outlet_statuses(self) -> Dict[int, bool]:
        """Get status of all outlets."""
        response = await self.send_command("show outlets all")
        statuses: Dict[int, bool] = {}
        for match in re.finditer(
            r"Outlet (\d+).*?Power State: (\w+)", response, re.DOTALL
        ):
            outlet = int(match.group(1))
            state = match.group(2) == "On"
            statuses[outlet] = state
        return statuses

    async def get_device_info(self) -> Dict[str, Any]:
        """Get device information."""
        return {
            "name": self.pdu_name,
            "model": self.pdu_model,
            "firmware": self.pdu_firmware,
            "serial": self.pdu_serial,
            "mac": self.pdu_mac,
            "available": self._available,
            "last_error": self._last_error,
            "last_error_time": self._last_error_time,
            "last_update": datetime.fromtimestamp(self._last_update, timezone.utc),
        }

    def get_model_capabilities(self) -> Dict[str, Any]:
        """Get capabilities based on model."""
        # Default capabilities
        capabilities = {
            "num_outlets": 8,  # Default to 8 outlets
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
        if "RLNK-P415" in model:
            capabilities["num_outlets"] = 4
            capabilities["max_current"] = 15

        # RLNK-P420: 4 outlets, 20A
        elif "RLNK-P420" in model:
            capabilities["num_outlets"] = 4
            capabilities["max_current"] = 20

        # RLNK-P915R: 9 outlets, 15A
        elif "RLNK-P915R" in model:
            capabilities["num_outlets"] = 9
            capabilities["max_current"] = 15

        # RLNK-P915R-SP: 9 outlets, 15A, surge protection
        elif "RLNK-P915R-SP" in model:
            capabilities["num_outlets"] = 9
            capabilities["max_current"] = 15
            capabilities["has_surge_protection"] = True

        # RLNK-P920R: 9 outlets, 20A
        elif "RLNK-P920R" in model:
            capabilities["num_outlets"] = 9
            capabilities["max_current"] = 20

        # RLNK-P920R-SP: 9 outlets, 20A, surge protection
        elif "RLNK-P920R-SP" in model:
            capabilities["num_outlets"] = 9
            capabilities["max_current"] = 20
            capabilities["has_surge_protection"] = True

        _LOGGER.debug("Model capabilities for %s: %s", model, capabilities)
        return capabilities

    async def update(self):
        """Update outlet states and other data."""
        if not self._connected:
            _LOGGER.warning("Not connected, attempting to reconnect before update")
            try:
                await self._background_connect()
                # If connection fails, the error will be logged in _background_connect
                if not self._connected:
                    return False
            except Exception as e:
                _LOGGER.error("Failed to reconnect during update: %s", e)
                return False

        _LOGGER.debug("Updating PDU data")

        try:
            # Get all outlet states - this has been improved to query each outlet individually
            update_success = await self.get_all_outlet_states()

            # Only update timestamp if we actually got some data
            if update_success:
                self._last_update = time.time()
                self._available = True
                _LOGGER.debug("Update completed successfully")
                return True
            else:
                _LOGGER.warning("Update failed - couldn't get outlet states")
                # Don't mark as unavailable on a single failure
                return False

        except Exception as e:
            _LOGGER.error("Error during update: %s", e)
            # Don't mark as unavailable on a single error
            return False
