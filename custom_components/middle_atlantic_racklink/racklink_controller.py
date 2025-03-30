"""Controller for Middle Atlantic Racklink PDU devices."""

import asyncio
import logging
import re
import telnetlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .const import COMMAND_TIMEOUT, SUPPORTED_MODELS

_LOGGER = logging.getLogger(__name__)


class RacklinkController:
    """Controller for Middle Atlantic RackLink devices."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        model: str = "AUTO_DETECT",
    ):
        """Initialize the controller."""
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.specified_model = model  # Store the user-specified model
        self.telnet: Optional[telnetlib.Telnet] = None
        self.response_cache = ""
        self.last_cmd = ""
        self.context = ""
        self.pdu_name = f"Racklink PDU ({host})"  # Default name until we get real one
        self.pdu_firmware = ""
        self.pdu_serial = f"{host}_{port}"  # Default identifier until we get real one
        self.pdu_mac = ""
        self.pdu_model = "Racklink PDU"  # Default model until we get real one
        self.outlet_states: Dict[int, bool] = {}
        self.outlet_names: Dict[int, str] = {}
        self.outlet_power: Dict[int, float] = {}
        self.outlet_current: Dict[int, float] = {}
        self.outlet_energy: Dict[int, float] = {}
        self.outlet_power_factor: Dict[int, float] = {}
        self.outlet_apparent_power: Dict[int, float] = {}
        self.outlet_voltage: Dict[int, float] = {}
        self.outlet_line_frequency: Dict[int, float] = {}
        self.outlet_cycling_period: Dict[int, int] = {}
        self.outlet_non_critical: Dict[int, bool] = {}
        self.outlet_receptacle_type: Dict[int, str] = {}
        self.outlet_rated_current: Dict[int, float] = {}
        self.outlet_operating_voltage: Dict[int, str] = {}
        self.sensors: Dict[str, float] = {}
        self._connected = False
        self._last_update = 0
        self._update_interval = 60  # seconds
        self._last_error: Optional[str] = None
        self._last_error_time: Optional[datetime] = None
        self._available = True
        self._connection_lock = asyncio.Lock()
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 3
        self._telnet_timeout = 5  # Shorter timeout for better responsiveness
        self._command_timeout = 15  # Longer timeout for commands
        self._connection_timeout = 20  # Longer timeout for initial connection
        self._command_delay = 1.0  # Delay between commands to avoid overwhelming device
        self._last_command_time = 0  # Timestamp of last command sent
        self._command_queue = asyncio.Queue()  # Queue for commands
        self._command_lock = asyncio.Lock()  # Lock for command processing
        self._command_processor_task = None  # Task for processing commands
        self._connection_task = None
        self._shutdown_requested = False

    @property
    def connected(self) -> bool:
        """Return if we are connected to the device."""
        # Only consider truly connected if we have a telnet object and _connected flag
        return self._connected and self.telnet is not None

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

    async def _create_telnet_connection(self):
        """Create a telnet connection in a separate thread to avoid blocking."""
        try:
            # Create telnet connection in a separate thread
            _LOGGER.debug("Creating telnet connection to %s:%s", self.host, self.port)
            result = await asyncio.to_thread(
                telnetlib.Telnet, self.host, self.port, self._telnet_timeout
            )
            if result:
                _LOGGER.debug("Successfully created telnet connection")
            else:
                _LOGGER.error("Failed to create telnet connection - returned None")
            return result
        except Exception as e:
            _LOGGER.error("Failed to create telnet connection: %s", e)
            return None

    async def _telnet_read_until(self, pattern: bytes, timeout: int = None) -> bytes:
        """Read from telnet connection in a non-blocking way."""
        if not self.telnet:
            raise ConnectionError("No telnet connection available")

        if pattern is None:
            raise ValueError("Pattern cannot be None")

        timeout = timeout or self._telnet_timeout
        try:
            _LOGGER.debug("Reading from telnet until pattern: %s", pattern)
            return await asyncio.to_thread(self.telnet.read_until, pattern, timeout)
        except EOFError as exc:
            self._connected = False
            raise ConnectionError("Connection closed by remote host") from exc
        except asyncio.CancelledError:
            _LOGGER.warning("Telnet read operation was cancelled")
            raise
        except Exception as exc:
            self._connected = False
            raise ConnectionError(f"Error reading from telnet: {exc}") from exc

    async def _telnet_write(self, data: bytes) -> None:
        """Write to telnet connection in a non-blocking way."""
        if not self.telnet:
            raise ConnectionError("No telnet connection available")

        if data is None:
            raise ValueError("Data cannot be None")

        try:
            _LOGGER.debug("Writing %d bytes to telnet", len(data))
            await asyncio.to_thread(self.telnet.write, data)
        except EOFError as exc:
            self._connected = False
            raise ConnectionError("Connection closed while writing data") from exc
        except asyncio.CancelledError:
            _LOGGER.warning("Telnet write operation was cancelled")
            raise
        except Exception as exc:
            self._connected = False
            raise ConnectionError(f"Error writing to telnet: {exc}") from exc

    async def start_background_connection(self) -> None:
        """Start background connection task that won't block Home Assistant startup."""
        if self._connection_task is not None and not self._connection_task.done():
            _LOGGER.debug("Background connection task already running")
            return

        _LOGGER.debug("Starting background connection for %s", self.host)
        self._connection_task = asyncio.create_task(self._background_connect())

    async def _background_connect(self) -> None:
        """Connect in background to avoid blocking Home Assistant."""
        try:
            # Attempt connection with a timeout but don't load initial status
            # This will be handled separately to prevent blocking
            await asyncio.wait_for(
                self._connect_only(), timeout=self._telnet_timeout * 2
            )

            # Once connected, start loading data in separate tasks
            if self._connected:
                asyncio.create_task(self._load_initial_data())

        except asyncio.TimeoutError:
            _LOGGER.error(
                "Background connection to %s timed out, will retry on first update",
                self.host,
            )
        except Exception as err:
            _LOGGER.error("Error in background connection to %s: %s", self.host, err)

    async def _connect_only(self) -> None:
        """Connect to the device but skip loading initial status."""
        if self._shutdown_requested:
            _LOGGER.debug("Shutdown requested, not connecting to %s", self.host)
            return

        async with self._connection_lock:
            if self._connected:
                _LOGGER.debug("Already connected to %s, skipping connection", self.host)
                return

            _LOGGER.info(
                "Connecting to Middle Atlantic Racklink at %s:%s", self.host, self.port
            )
            try:
                # Create telnet connection with timeout
                self.telnet = await asyncio.wait_for(
                    self._create_telnet_connection(), timeout=self._telnet_timeout
                )

                if not self.telnet:
                    raise ConnectionError(
                        f"Failed to connect to {self.host}:{self.port}"
                    )

                await self.login()
                self._connected = True
                self._available = True
                self._last_error = None
                self._last_error_time = None
                self._reconnect_attempts = 0
                _LOGGER.info(
                    "Successfully connected to Middle Atlantic Racklink at %s (basic connection)",
                    self.host,
                )
            except (asyncio.TimeoutError, ConnectionError) as e:
                if self.telnet:
                    await asyncio.to_thread(self.telnet.close)
                    self.telnet = None
                self._connected = False
                self._handle_error(f"Connection failed: {e}")
                raise ValueError(f"Connection failed: {e}") from e
            except Exception as e:
                if self.telnet:
                    await asyncio.to_thread(self.telnet.close)
                    self.telnet = None
                self._connected = False
                self._handle_error(f"Unexpected error during connection: {e}")
                raise ValueError(f"Connection failed: {e}") from e

    async def _load_initial_data(self) -> None:
        """Load initial data in a separate non-blocking task."""
        if not self._connected:
            _LOGGER.warning("Cannot load initial data, not connected")
            return

        _LOGGER.debug("Starting to load initial device data for %s", self.host)

        # Try to load data with timeouts for each phase
        try:
            # Phase 1: Get PDU details (needed for basic operation)
            _LOGGER.debug("Loading PDU details for %s", self.host)
            try:
                await asyncio.wait_for(
                    self.get_pdu_details(), timeout=self._command_timeout
                )
            except asyncio.TimeoutError:
                _LOGGER.warning("Timeout getting PDU details, continuing with defaults")
            except Exception as e:
                _LOGGER.warning(
                    "Error getting PDU details: %s, continuing with defaults", e
                )

            # Phase 2: Get outlet states (after a short delay)
            await asyncio.sleep(0.5)  # Small delay to not overload the device
            if not self._connected:
                return

            _LOGGER.debug("Loading outlet states for %s", self.host)
            try:
                await asyncio.wait_for(
                    self.get_all_outlet_states(), timeout=self._command_timeout
                )
            except asyncio.TimeoutError:
                _LOGGER.warning(
                    "Timeout getting outlet states, will retry on next update"
                )
            except Exception as e:
                _LOGGER.warning(
                    "Error getting outlet states: %s, will retry on next update", e
                )

            # Phase 3: Get sensor values (after another delay)
            await asyncio.sleep(0.5)  # Small delay to not overload the device
            if not self._connected:
                return

            _LOGGER.debug("Loading sensor values for %s", self.host)
            try:
                await asyncio.wait_for(
                    self.get_sensor_values(), timeout=self._command_timeout
                )
            except asyncio.TimeoutError:
                _LOGGER.warning(
                    "Timeout getting sensor values, will retry on next update"
                )
            except Exception as e:
                _LOGGER.warning(
                    "Error getting sensor values: %s, will retry on next update", e
                )

            self._last_update = asyncio.get_event_loop().time()
            _LOGGER.info("Successfully loaded all initial data for %s", self.host)

        except asyncio.TimeoutError:
            _LOGGER.error("Timeout loading initial data for %s", self.host)
            # Don't lose the connection just because initial load timed out
            self._available = True  # Keep device available for commands
        except Exception as err:
            _LOGGER.error("Error loading initial data for %s: %s", self.host, err)
            # Don't lose the connection just because of an error in initial load
            self._available = True  # Keep device available for commands

    async def connect(self) -> None:
        """Connect to the RackLink device."""
        # Don't connect if shutdown was requested
        if self._shutdown_requested:
            _LOGGER.debug("Shutdown requested, not connecting to %s", self.host)
            return

        async with self._connection_lock:
            if self._connected:
                _LOGGER.debug("Already connected to %s, skipping connection", self.host)
                return

            _LOGGER.info(
                "Connecting to Middle Atlantic Racklink at %s:%s", self.host, self.port
            )
            try:
                # Create telnet connection with timeout
                self.telnet = await asyncio.wait_for(
                    self._create_telnet_connection(), timeout=self._connection_timeout
                )

                if not self.telnet:
                    raise ConnectionError(
                        f"Failed to connect to {self.host}:{self.port}"
                    )

                # Reset the telnet object's timeout to our command timeout
                if hasattr(self.telnet, "timeout"):
                    self.telnet.timeout = self._command_timeout

                await self.login()
                # Use the separate initial status loading method
                asyncio.create_task(self._load_initial_data())

                # Start the command processor task
                if (
                    self._command_processor_task is None
                    or self._command_processor_task.done()
                ):
                    self._command_processor_task = asyncio.create_task(
                        self._process_command_queue()
                    )
                    _LOGGER.debug("Started command processor task")

                self._connected = True
                self._available = True
                self._last_error = None
                self._last_error_time = None
                self._reconnect_attempts = 0
                _LOGGER.info(
                    "Successfully connected to Middle Atlantic Racklink at %s",
                    self.host,
                )
            except (asyncio.TimeoutError, ConnectionError) as e:
                _LOGGER.error(
                    "Connection to %s failed due to timeout or connection error: %s",
                    self.host,
                    e,
                )
                if self.telnet:
                    _LOGGER.debug("Closing telnet connection due to connection error")
                    await asyncio.to_thread(self.telnet.close)
                    self.telnet = None
                self._connected = False
                self._handle_error(f"Connection failed: {e}")
                raise ValueError(f"Connection failed: {e}") from e
            except Exception as e:
                _LOGGER.error(
                    "Connection to %s failed due to unexpected error: %s", self.host, e
                )
                if self.telnet:
                    _LOGGER.debug("Closing telnet connection due to unexpected error")
                    await asyncio.to_thread(self.telnet.close)
                    self.telnet = None
                self._connected = False
                self._handle_error(f"Unexpected error during connection: {e}")
                raise ValueError(f"Connection failed: {e}") from e

    async def reconnect(self) -> None:
        """Reconnect to the device with backoff."""
        if self._shutdown_requested:
            _LOGGER.debug("Shutdown requested, not reconnecting to %s", self.host)
            return

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
        await self.disconnect()

        try:
            await asyncio.wait_for(self.connect(), timeout=self._telnet_timeout)
            _LOGGER.info("Successfully reconnected to %s", self.host)
        except (asyncio.TimeoutError, Exception) as e:
            _LOGGER.error("Failed to reconnect to %s: %s", self.host, e)

    async def send_command(self, cmd: str) -> str:
        """Queue a command to be sent to the device and return the response."""
        if self._shutdown_requested:
            _LOGGER.debug("Shutdown requested, not sending command to %s", self.host)
            return ""

        if not self.connected:
            _LOGGER.debug("Not connected, attempting to connect before sending command")
            try:
                # Use a longer timeout for connection attempts
                await asyncio.wait_for(self.connect(), timeout=self._connection_timeout)
            except asyncio.TimeoutError:
                _LOGGER.error("Connection attempt timed out")
                return ""
            except Exception as err:
                _LOGGER.error("Connection attempt failed: %s", err)
                return ""

            if not self.connected:
                _LOGGER.error("Still not connected after connection attempt")
                return ""

        # Create a future to get the result
        future = asyncio.get_running_loop().create_future()

        # Add the command to the queue
        await self._command_queue.put((cmd, future))

        # Wait for the result
        try:
            response = await asyncio.wait_for(future, timeout=self._command_timeout)
            return response
        except asyncio.TimeoutError:
            _LOGGER.error("Command timed out waiting for result: %s", cmd)
            return ""
        except Exception as e:
            _LOGGER.error("Error waiting for command result: %s - %s", cmd, e)
            return ""

    async def _process_command_queue(self):
        """Process commands from the queue with rate limiting."""
        while not self._shutdown_requested:
            try:
                # Get the next command from the queue
                cmd, future = await self._command_queue.get()

                try:
                    # Enforce delay between commands
                    now = asyncio.get_event_loop().time()
                    time_since_last = now - self._last_command_time
                    if time_since_last < self._command_delay:
                        delay = self._command_delay - time_since_last
                        _LOGGER.debug(
                            "Rate limiting: Waiting %.2f seconds before next command",
                            delay,
                        )
                        await asyncio.sleep(delay)

                    # Process the command
                    result = await self._send_command_internal(cmd)
                    self._last_command_time = asyncio.get_event_loop().time()

                    # Set the result in the future
                    if not future.done():
                        future.set_result(result)
                except Exception as e:
                    if not future.done():
                        future.set_exception(e)
                finally:
                    # Mark the task as done
                    self._command_queue.task_done()

            except asyncio.CancelledError:
                _LOGGER.debug("Command processor task cancelled")
                break
            except Exception as e:
                _LOGGER.error("Error in command processor: %s", e)
                await asyncio.sleep(1)  # Prevent tight loop if there's an error

        _LOGGER.debug("Command processor task exiting")

    async def _send_command_internal(self, cmd: str) -> str:
        """Send a command to the device and return the response."""
        try:
            _LOGGER.debug("Sending command: %s", cmd)
            # Make sure we have a valid telnet connection
            if not self.telnet:
                _LOGGER.error("No telnet connection available, attempting to reconnect")
                await self.reconnect()
                if not self.connected or not self.telnet:
                    _LOGGER.error("Failed to establish telnet connection")
                    return ""

            # Make sure to include CRLF - this is critical for telnet protocol
            await self._telnet_write(f"{cmd}\r\n".encode())
            self.last_cmd = cmd
            self.context = cmd.replace(" ", "")

            # Read until prompt character
            _LOGGER.debug("Waiting for response to command: %s", cmd)
            response = await asyncio.wait_for(
                self._telnet_read_until(b"#", self._telnet_timeout),
                timeout=self._command_timeout,  # Use a longer timeout for commands
            )

            if response:
                # Decode and clean up the response
                self.response_cache = response.decode("utf-8", errors="ignore")
                _LOGGER.debug(
                    "Response (first 100 chars): %s",
                    self.response_cache[:100].replace("\r\n", " "),
                )

                # Add a small delay after receiving response to make sure device is ready for next command
                await asyncio.sleep(0.2)

                return self.response_cache
            else:
                _LOGGER.warning("Empty response for command: %s", cmd)
                return ""

        except asyncio.TimeoutError:
            _LOGGER.error(
                "Command timed out after %s seconds: %s",
                self._command_timeout,
                cmd,
            )
            self._handle_error(f"Command timed out: {cmd}")
            # Attempt to reconnect on timeout
            await self.reconnect()
            # Don't attempt reconnect immediately on timeout, just return empty
            return ""
        except ConnectionError as e:
            _LOGGER.error("Connection error while sending command: %s", e)
            self._handle_error(f"Connection error while sending command: {e}")
            await self.reconnect()
            # After reconnection, try sending the command again if we're connected
            if self.connected:
                return await self.send_command(cmd)
            return ""
        except Exception as e:
            _LOGGER.error("Unexpected error sending command: %s - %s", cmd, e)
            self._handle_error(f"Error sending command: {e}")
            await self.reconnect()
            return ""

    async def login(self) -> None:
        """Login to the device."""
        try:
            # Wait for username prompt with timeout
            _LOGGER.debug("Waiting for Username prompt")
            response = await asyncio.wait_for(
                self._telnet_read_until(b"Username:", self._connection_timeout),
                timeout=self._connection_timeout,
            )
            _LOGGER.debug("Got Username prompt, sending username")

            # Send username with CRLF
            await self._telnet_write(f"{self.username}\r\n".encode())

            # Wait for password prompt with timeout
            _LOGGER.debug("Waiting for Password prompt")
            response = await asyncio.wait_for(
                self._telnet_read_until(b"Password:", self._connection_timeout),
                timeout=self._connection_timeout,
            )
            _LOGGER.debug("Got Password prompt, sending password")

            # Send password with CRLF
            await self._telnet_write(f"{self.password}\r\n".encode())

            # Wait for command prompt with timeout
            _LOGGER.debug("Waiting for command prompt")
            response = await asyncio.wait_for(
                self._telnet_read_until(b"#", self._connection_timeout),
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

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        async with self._connection_lock:
            if self.telnet:
                try:
                    # Close telnet connection in a thread to avoid blocking
                    await asyncio.to_thread(self.telnet.close)
                except Exception as e:
                    _LOGGER.warning("Error closing telnet connection: %s", e)
                finally:
                    self.telnet = None
                    self._connected = False

    async def shutdown(self) -> None:
        """Clean shutdown of the controller."""
        _LOGGER.debug("Shutting down Racklink controller for %s", self.host)
        self._shutdown_requested = True

        # Cancel command processor task
        if self._command_processor_task and not self._command_processor_task.done():
            self._command_processor_task.cancel()
            try:
                await self._command_processor_task
            except asyncio.CancelledError:
                pass

        # Cancel any pending connection task
        if self._connection_task and not self._connection_task.done():
            self._connection_task.cancel()
            try:
                await self._connection_task
            except asyncio.CancelledError:
                pass

        await self.disconnect()

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

    async def get_all_outlet_states(self, force_refresh=False):
        """Get all outlet states and details."""
        _LOGGER.debug(
            "Getting all outlet states%s", " (forced refresh)" if force_refresh else ""
        )
        try:
            response = await self.send_command("show outlets all details")
            if not response:
                _LOGGER.error("No response when getting outlet states")
                # Fallback: maintain existing state data rather than clearing it
                return

            _LOGGER.debug("Outlet states response length: %d", len(response))
            _LOGGER.debug(
                "First 100 chars of response: %s", response[:100].replace("\r\n", " ")
            )

            # Add more diagnostic logging - if we have serious issues, log the full response
            _LOGGER.debug(
                "Full outlet states response: %s", response.replace("\r\n", " ")
            )

            # Try direct approach first - look for each outlet individually
            for outlet_num in range(1, 10):  # Try outlets 1-9
                outlet_pattern = re.compile(
                    f"Outlet {outlet_num}.*?Power state:\\s*(\\w+)",
                    re.DOTALL | re.IGNORECASE,
                )
                outlet_match = outlet_pattern.search(response)
                if outlet_match:
                    raw_state = outlet_match.group(1)
                    state = raw_state.lower() in ["on", "1", "true", "yes", "active"]
                    self.outlet_states[outlet_num] = state
                    _LOGGER.debug(
                        "Direct match - Outlet %d state: %s (raw: %s)",
                        outlet_num,
                        state,
                        raw_state,
                    )

            # If we found any outlets using the direct approach, skip the complex parsing
            if self.outlet_states:
                _LOGGER.debug(
                    "Found %d outlets using direct pattern matching",
                    len(self.outlet_states),
                )
                # Now get the names using a simpler approach too
                for outlet_num in range(1, 10):
                    if outlet_num in self.outlet_states:
                        name_pattern = re.compile(
                            f"Outlet {outlet_num}[^\\n]*\\n(.*?)\\n", re.DOTALL
                        )
                        name_match = name_pattern.search(response)
                        if name_match:
                            name = name_match.group(1).strip()
                            # Sometimes the name contains the outlet number like "1 - Name"
                            if " - " in name:
                                name = name.split(" - ", 1)[1].strip()
                            if name:
                                self.outlet_names[outlet_num] = name
                                _LOGGER.debug(
                                    "Found outlet %d name: %s", outlet_num, name
                                )
                            else:
                                self.outlet_names[outlet_num] = f"Outlet {outlet_num}"
                        else:
                            self.outlet_names[outlet_num] = f"Outlet {outlet_num}"

                # Return early if we got the states using the direct approach
                return

            # Using a pattern inspired by the Q-Sys Lua implementation
            # Looking for patterns like:
            # Outlet 1:
            # 1 - Outlet Name
            # Power state: On
            # RMS Current: 0.00A
            # Active Power: 0W
            # Active Energy: 0Wh
            # Power Factor: 0%

            # First, we'll split the response into sections by outlet
            try:
                # Pattern to match outlet headers and capture the number
                outlet_headers = re.findall(r"Outlet (\d+):", response)
                _LOGGER.debug(
                    "Found %d outlet headers: %s", len(outlet_headers), outlet_headers
                )

                if not outlet_headers:
                    _LOGGER.warning("No outlet headers found in response")
                    return

                # Split the response by "Outlet X:" patterns
                sections = re.split(r"Outlet \d+:", response)

                # Skip the first section (before the first outlet)
                if sections and len(sections) > 1:
                    sections = sections[1:]

                    for i, (outlet_num, section) in enumerate(
                        zip(outlet_headers, sections)
                    ):
                        try:
                            outlet = int(outlet_num)
                            _LOGGER.debug("Processing outlet %d data", outlet)

                            # Extract state - more flexible pattern to match various formats
                            state_match = re.search(
                                r"Power state:\s*(\w+)", section, re.IGNORECASE
                            )
                            if state_match:
                                raw_state = state_match.group(1)
                                _LOGGER.debug("Raw outlet state text: '%s'", raw_state)

                                # More permissive checking - any variant of "on" is considered on
                                state = raw_state.lower() in [
                                    "on",
                                    "1",
                                    "true",
                                    "yes",
                                    "active",
                                ]
                                self.outlet_states[outlet] = state
                                _LOGGER.debug(
                                    "Outlet %d state: %s (raw: %s)",
                                    outlet,
                                    state,
                                    raw_state,
                                )
                            else:
                                _LOGGER.warning(
                                    "No power state found for outlet %d", outlet
                                )

                            # Extract name - looking for patterns like "1 - Office Equipment"
                            name_match = re.search(r"(\d+)\s*-\s*(.+?)[\r\n]", section)
                            if name_match and int(name_match.group(1)) == outlet:
                                name = name_match.group(2).strip()
                                self.outlet_names[outlet] = name
                                _LOGGER.debug("Outlet %d name: %s", outlet, name)
                            else:
                                # Fallback - just use generic name if we can't find one
                                if outlet not in self.outlet_names:
                                    self.outlet_names[outlet] = f"Outlet {outlet}"

                            # Extract current - including '0.00A' or '0 A' formats
                            current_match = re.search(
                                r"RMS Current:\s*([\d.]+)\s*A", section, re.IGNORECASE
                            )
                            if current_match:
                                try:
                                    current = float(current_match.group(1).strip())
                                    self.outlet_current[outlet] = current
                                    _LOGGER.debug(
                                        "Outlet %d current: %.2f A", outlet, current
                                    )
                                except ValueError:
                                    _LOGGER.warning(
                                        "Invalid current value for outlet %d", outlet
                                    )

                            # Extract power - including '0W' or '0 W' formats
                            power_match = re.search(
                                r"Active Power:\s*([\d.]+)\s*W", section, re.IGNORECASE
                            )
                            if power_match:
                                try:
                                    power = float(power_match.group(1).strip())
                                    self.outlet_power[outlet] = power
                                    _LOGGER.debug(
                                        "Outlet %d power: %.2f W", outlet, power
                                    )
                                except ValueError:
                                    _LOGGER.warning(
                                        "Invalid power value for outlet %d", outlet
                                    )

                            # Extract energy - including '0Wh' or '0 Wh' formats
                            energy_match = re.search(
                                r"Active Energy:\s*([\d.]+)\s*Wh",
                                section,
                                re.IGNORECASE,
                            )
                            if energy_match:
                                try:
                                    energy = float(energy_match.group(1).strip())
                                    self.outlet_energy[outlet] = energy
                                    _LOGGER.debug(
                                        "Outlet %d energy: %.2f Wh", outlet, energy
                                    )
                                except ValueError:
                                    _LOGGER.warning(
                                        "Invalid energy value for outlet %d", outlet
                                    )

                            # Extract power factor - including '0%' format
                            pf_match = re.search(
                                r"Power Factor:\s*([\d.]+)\s*%", section, re.IGNORECASE
                            )
                            if pf_match:
                                try:
                                    power_factor = float(pf_match.group(1).strip())
                                    self.outlet_power_factor[outlet] = (
                                        power_factor / 100.0
                                    )  # Convert to decimal
                                    _LOGGER.debug(
                                        "Outlet %d power factor: %.2f",
                                        outlet,
                                        self.outlet_power_factor[outlet],
                                    )
                                except ValueError:
                                    _LOGGER.warning(
                                        "Invalid power factor value for outlet %d",
                                        outlet,
                                    )

                            # Extract apparent power (VA)
                            apparent_power_match = re.search(
                                r"Apparent Power:\s*([\d.]+)\s*VA",
                                section,
                                re.IGNORECASE,
                            )
                            if apparent_power_match:
                                try:
                                    apparent_power = float(
                                        apparent_power_match.group(1).strip()
                                    )
                                    self.outlet_apparent_power[outlet] = apparent_power
                                    _LOGGER.debug(
                                        "Outlet %d apparent power: %.2f VA",
                                        outlet,
                                        apparent_power,
                                    )
                                except ValueError:
                                    _LOGGER.warning(
                                        "Invalid apparent power value for outlet %d",
                                        outlet,
                                    )

                            # Extract RMS Voltage
                            voltage_match = re.search(
                                r"RMS Voltage:\s*([\d.]+)\s*V", section, re.IGNORECASE
                            )
                            if voltage_match:
                                try:
                                    voltage = float(voltage_match.group(1).strip())
                                    self.outlet_voltage[outlet] = voltage
                                    _LOGGER.debug(
                                        "Outlet %d voltage: %.1f V", outlet, voltage
                                    )
                                except ValueError:
                                    _LOGGER.warning(
                                        "Invalid voltage value for outlet %d", outlet
                                    )

                            # Extract Line Frequency
                            freq_match = re.search(
                                r"Line Frequency:\s*([\d.]+)\s*Hz",
                                section,
                                re.IGNORECASE,
                            )
                            if freq_match:
                                try:
                                    frequency = float(freq_match.group(1).strip())
                                    self.outlet_line_frequency[outlet] = frequency
                                    _LOGGER.debug(
                                        "Outlet %d frequency: %.1f Hz",
                                        outlet,
                                        frequency,
                                    )
                                except ValueError:
                                    _LOGGER.warning(
                                        "Invalid frequency value for outlet %d", outlet
                                    )

                            # Extract Cycling power off period
                            cycle_match = re.search(
                                r"Cycling power off period:.*?(\d+)\s*s",
                                section,
                                re.IGNORECASE,
                            )
                            if cycle_match:
                                try:
                                    cycling_period = int(cycle_match.group(1).strip())
                                    self.outlet_cycling_period[outlet] = cycling_period
                                    _LOGGER.debug(
                                        "Outlet %d cycling period: %d s",
                                        outlet,
                                        cycling_period,
                                    )
                                except ValueError:
                                    _LOGGER.warning(
                                        "Invalid cycling period value for outlet %d",
                                        outlet,
                                    )

                            # Extract Non-critical flag
                            non_critical_match = re.search(
                                r"Non critical:\s*(True|False|Yes|No)",
                                section,
                                re.IGNORECASE,
                            )
                            if non_critical_match:
                                non_critical_value = (
                                    non_critical_match.group(1).strip().lower()
                                )
                                is_non_critical = non_critical_value in ["true", "yes"]
                                self.outlet_non_critical[outlet] = is_non_critical
                                _LOGGER.debug(
                                    "Outlet %d non-critical: %s",
                                    outlet,
                                    is_non_critical,
                                )

                            # Extract Receptacle type
                            receptacle_match = re.search(
                                r"Receptacle type:\s*(.+?)[\r\n]",
                                section,
                                re.IGNORECASE,
                            )
                            if receptacle_match:
                                receptacle_type = receptacle_match.group(1).strip()
                                self.outlet_receptacle_type[outlet] = receptacle_type
                                _LOGGER.debug(
                                    "Outlet %d receptacle type: %s",
                                    outlet,
                                    receptacle_type,
                                )

                            # Extract Rated current
                            rated_current_match = re.search(
                                r"Rated current:\s*([\d.]+)\s*A", section, re.IGNORECASE
                            )
                            if rated_current_match:
                                try:
                                    rated_current = float(
                                        rated_current_match.group(1).strip()
                                    )
                                    self.outlet_rated_current[outlet] = rated_current
                                    _LOGGER.debug(
                                        "Outlet %d rated current: %.1f A",
                                        outlet,
                                        rated_current,
                                    )
                                except ValueError:
                                    _LOGGER.warning(
                                        "Invalid rated current value for outlet %d",
                                        outlet,
                                    )

                            # Extract Operating voltage
                            op_voltage_match = re.search(
                                r"Operating voltage:\s*(.+?)[\r\n]",
                                section,
                                re.IGNORECASE,
                            )
                            if op_voltage_match:
                                operating_voltage = op_voltage_match.group(1).strip()
                                self.outlet_operating_voltage[outlet] = (
                                    operating_voltage
                                )
                                _LOGGER.debug(
                                    "Outlet %d operating voltage: %s",
                                    outlet,
                                    operating_voltage,
                                )

                        except Exception as err:
                            _LOGGER.error("Error parsing outlet %d: %s", outlet, err)
                            _LOGGER.debug(
                                "Section causing parse error: %s", section[:100]
                            )
                else:
                    _LOGGER.warning("No valid outlet sections found in response")

            except Exception as e:
                _LOGGER.error("Failed to parse outlet details: %s", e)
                _LOGGER.debug("Response causing parse failure: %s", response[:200])

        except Exception as e:
            _LOGGER.error("Failed to get outlet states: %s", e)
            # Don't clear existing states on error - maintain last known good values
            return

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
        cmd = f"power outlets {outlet} cycle /y"
        await self.send_command(cmd)

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
        cmd = "power outlets all cycle /y"
        await self.send_command(cmd)

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
        """Update all device data."""
        _LOGGER.debug("Running update for %s", self.host)
        if not self.connected:
            _LOGGER.debug("Not connected, attempting to connect before update")
            try:
                # Use longer timeout for connection during update
                await asyncio.wait_for(self.connect(), timeout=self._connection_timeout)
            except asyncio.TimeoutError:
                _LOGGER.error("Connection attempt during update timed out")
                return
            except Exception as err:
                _LOGGER.error("Connection attempt during update failed: %s", err)
                return

        # Get PDU details if we don't have them yet
        if not self.pdu_serial or self.pdu_serial == f"{self.host}_{self.port}":
            try:
                await asyncio.wait_for(
                    self.get_pdu_details(), timeout=self._command_timeout
                )
            except asyncio.TimeoutError:
                _LOGGER.warning("Timeout getting PDU details, continuing with defaults")
            except Exception as e:
                _LOGGER.warning(
                    "Error getting PDU details: %s, continuing with defaults", e
                )

        # Get outlet states (this will also update outlet metrics)
        _LOGGER.debug("Updating outlet states for %s", self.host)
        try:
            await asyncio.wait_for(
                self.get_all_outlet_states(force_refresh=True),
                timeout=self._command_timeout,
            )
            _LOGGER.debug(
                "After update, found %d outlet states", len(self.outlet_states)
            )
            for outlet, state in self.outlet_states.items():
                _LOGGER.debug("  Outlet %d: %s", outlet, "ON" if state else "OFF")
        except Exception as err:
            _LOGGER.error("Failed to update outlet states: %s", err)

        # If outlet states are still empty, try the individual approach
        if not self.outlet_states:
            _LOGGER.debug("No outlet states found, trying individual outlet queries")
            for outlet_num in range(1, 10):  # Try outlets 1-9
                try:
                    response = await self.send_command(
                        f"show outlets {outlet_num} details"
                    )
                    if response:
                        state_match = re.search(
                            r"Power state:\s*(\w+)", response, re.IGNORECASE
                        )
                        if state_match:
                            raw_state = state_match.group(1)
                            state = raw_state.lower() in [
                                "on",
                                "1",
                                "true",
                                "yes",
                                "active",
                            ]
                            self.outlet_states[outlet_num] = state
                            _LOGGER.debug(
                                "Individual query - Outlet %d state: %s (raw: %s)",
                                outlet_num,
                                state,
                                raw_state,
                            )

                            # Also extract the name while we're here
                            name_match = re.search(
                                r"Outlet \d+ - (.+?)[\r\n]", response
                            )
                            if name_match:
                                name = name_match.group(1).strip()
                                self.outlet_names[outlet_num] = name
                                _LOGGER.debug(
                                    "Found outlet %d name: %s", outlet_num, name
                                )
                            else:
                                self.outlet_names[outlet_num] = f"Outlet {outlet_num}"
                except Exception as err:
                    _LOGGER.debug("Error querying outlet %d: %s", outlet_num, err)

        # Get sensor values
        try:
            await asyncio.wait_for(
                self.get_sensor_values(force_refresh=True),
                timeout=self._command_timeout,
            )
            _LOGGER.debug("After update, sensors: %s", self.sensors)
        except Exception as err:
            _LOGGER.error("Failed to update sensor values: %s", err)

        self._last_update = asyncio.get_event_loop().time()
        self._available = True  # Mark as available even if some data is missing
