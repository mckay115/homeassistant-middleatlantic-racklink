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
            result = await asyncio.to_thread(
                telnetlib.Telnet, self.host, self.port, self._telnet_timeout
            )
            return result
        except Exception as e:
            _LOGGER.error("Failed to create telnet connection: %s", e)
            return None

    async def _telnet_read_until(self, pattern: bytes, timeout: int = None) -> bytes:
        """Read from telnet connection in a non-blocking way."""
        if not self.telnet:
            raise ConnectionError("No telnet connection available")

        timeout = timeout or self._telnet_timeout
        try:
            return await asyncio.to_thread(self.telnet.read_until, pattern, timeout)
        except EOFError as exc:
            self._connected = False
            raise ConnectionError("Connection closed by remote host") from exc

    async def _telnet_write(self, data: bytes) -> None:
        """Write to telnet connection in a non-blocking way."""
        if not self.telnet:
            raise ConnectionError("No telnet connection available")

        try:
            await asyncio.to_thread(self.telnet.write, data)
        except EOFError as exc:
            self._connected = False
            raise ConnectionError("Connection closed while writing data") from exc

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

        # Create a sequence of tasks but don't wait for their completion
        try:
            # Phase 1: Get PDU details (needed for basic operation)
            _LOGGER.debug("Loading PDU details for %s", self.host)
            await asyncio.wait_for(self.get_pdu_details(), timeout=self._telnet_timeout)

            # Phase 2: Get outlet states (after a short delay)
            await asyncio.sleep(0.5)  # Small delay to not overload the device
            if not self._connected:
                return

            _LOGGER.debug("Loading outlet states for %s", self.host)
            await asyncio.wait_for(
                self.get_all_outlet_states(), timeout=self._telnet_timeout
            )

            # Phase 3: Get sensor values (after another delay)
            await asyncio.sleep(0.5)  # Small delay to not overload the device
            if not self._connected:
                return

            _LOGGER.debug("Loading sensor values for %s", self.host)
            await asyncio.wait_for(
                self.get_sensor_values(), timeout=self._telnet_timeout
            )

            self._last_update = asyncio.get_event_loop().time()
            _LOGGER.info("Successfully loaded all initial data for %s", self.host)

        except asyncio.TimeoutError:
            _LOGGER.error("Timeout loading initial data for %s", self.host)
        except Exception as err:
            _LOGGER.error("Error loading initial data for %s: %s", self.host, err)

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
                    self._create_telnet_connection(), timeout=self._telnet_timeout
                )

                if not self.telnet:
                    raise ConnectionError(
                        f"Failed to connect to {self.host}:{self.port}"
                    )

                await self.login()
                # Use the separate initial status loading method
                asyncio.create_task(self._load_initial_data())
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
        """Send a command to the device and return the response."""
        if self._shutdown_requested:
            _LOGGER.debug("Shutdown requested, not sending command to %s", self.host)
            return ""

        if not self.connected:
            _LOGGER.debug("Not connected, attempting to connect before sending command")
            try:
                await asyncio.wait_for(self.connect(), timeout=self._telnet_timeout)
            except asyncio.TimeoutError:
                _LOGGER.error("Connection attempt timed out")
                return ""
            except Exception as err:
                _LOGGER.error("Connection attempt failed: %s", err)
                return ""

            if not self.connected:
                _LOGGER.error("Still not connected after connection attempt")
                return ""

        try:
            _LOGGER.debug("Sending command: %s", cmd)
            # Make sure to include CRLF - this is critical for telnet protocol
            await self._telnet_write(f"{cmd}\r\n".encode())
            self.last_cmd = cmd
            self.context = cmd.replace(" ", "")

            # Read until prompt character
            _LOGGER.debug("Waiting for response to command: %s", cmd)
            response = await asyncio.wait_for(
                self._telnet_read_until(b"#", self._telnet_timeout),
                timeout=self._telnet_timeout * 1.5,  # Add margin to outer timeout
            )

            if response:
                # Decode and clean up the response
                self.response_cache = response.decode("utf-8", errors="ignore")
                _LOGGER.debug(
                    "Response (first 100 chars): %s",
                    self.response_cache[:100].replace("\r\n", " "),
                )
                return self.response_cache
            else:
                _LOGGER.warning("Empty response for command: %s", cmd)
                return ""

        except asyncio.TimeoutError:
            _LOGGER.error(
                "Command timed out after %s seconds: %s",
                self._telnet_timeout * 1.5,
                cmd,
            )
            self._handle_error(f"Command timed out: {cmd}")
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
                self._telnet_read_until(b"Username:", self._telnet_timeout),
                timeout=self._telnet_timeout * 1.5,
            )
            _LOGGER.debug("Got Username prompt, sending username")

            # Send username with CRLF
            await self._telnet_write(f"{self.username}\r\n".encode())

            # Wait for password prompt with timeout
            _LOGGER.debug("Waiting for Password prompt")
            response = await asyncio.wait_for(
                self._telnet_read_until(b"Password:", self._telnet_timeout),
                timeout=self._telnet_timeout * 1.5,
            )
            _LOGGER.debug("Got Password prompt, sending password")

            # Send password with CRLF
            await self._telnet_write(f"{self.password}\r\n".encode())

            # Wait for command prompt with timeout
            _LOGGER.debug("Waiting for command prompt")
            response = await asyncio.wait_for(
                self._telnet_read_until(b"#", self._telnet_timeout),
                timeout=self._telnet_timeout * 1.5,
            )
            _LOGGER.debug("Got command prompt")

            if b"#" not in response:
                _LOGGER.error("Did not get command prompt after login")
                raise ValueError("Login failed: Invalid credentials")

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
        response = await self.send_command("show pdu details")
        if not response:
            _LOGGER.error("No response when getting PDU details")
            return

        _LOGGER.debug("PDU details response: %s", response[:200].replace("\r\n", " "))

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
                        "No model detected, using specified model: %s", self.pdu_model
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

    async def get_all_outlet_states(self, force_refresh=False):
        """Get all outlet states and details."""
        _LOGGER.debug(
            "Getting all outlet states%s", " (forced refresh)" if force_refresh else ""
        )
        response = await self.send_command("show outlets all details")
        if not response:
            _LOGGER.error("No response when getting outlet states")
            return

        _LOGGER.debug("Outlet states response length: %d", len(response))

        # Using a more flexible pattern that matches what we see in the Lua implementation
        # pattern = r"Outlet (\d+):\r\n(.*?)Power state: (On|Off).*?RMS Current: (.+)A.*?Active Power: (.+)W.*?Active Energy: (.+)Wh.*?Power Factor: (.+)%"
        outlet_sections = re.split(r"Outlet \d+:", response)

        # Skip the first section which is the header
        if len(outlet_sections) > 1:
            outlet_sections = outlet_sections[1:]

            for i, section in enumerate(outlet_sections):
                try:
                    outlet = i + 1  # Outlets are 1-indexed

                    # Extract state
                    state_match = re.search(r"Power state: (On|Off)", section)
                    if state_match:
                        state = state_match.group(1) == "On"
                        self.outlet_states[outlet] = state
                        _LOGGER.debug("Outlet %d state: %s", outlet, state)

                    # Extract name
                    name_match = re.search(r"Outlet \d+ - (.+?)[\r\n]", section)
                    if name_match:
                        name = name_match.group(1).strip()
                        self.outlet_names[outlet] = name
                        _LOGGER.debug("Outlet %d name: %s", outlet, name)

                    # Extract current
                    current_match = re.search(r"RMS Current: (.+?)A", section)
                    if current_match:
                        current = float(current_match.group(1).strip())
                        self.outlet_current[outlet] = current
                        _LOGGER.debug("Outlet %d current: %f", outlet, current)

                    # Extract power
                    power_match = re.search(r"Active Power: (.+?)W", section)
                    if power_match:
                        power = float(power_match.group(1).strip())
                        self.outlet_power[outlet] = power
                        _LOGGER.debug("Outlet %d power: %f", outlet, power)

                    # Extract energy
                    energy_match = re.search(r"Active Energy: (.+?)Wh", section)
                    if energy_match:
                        energy = float(energy_match.group(1).strip())
                        self.outlet_energy[outlet] = energy
                        _LOGGER.debug("Outlet %d energy: %f", outlet, energy)

                    # Extract power factor
                    pf_match = re.search(r"Power Factor: (.+?)%", section)
                    if pf_match:
                        power_factor = float(pf_match.group(1).strip())
                        self.outlet_power_factor[outlet] = power_factor
                        _LOGGER.debug(
                            "Outlet %d power factor: %f", outlet, power_factor
                        )

                except Exception as err:
                    _LOGGER.error("Error parsing outlet %d: %s", i + 1, err)
                    _LOGGER.debug("Section causing parse error: %s", section[:100])
        else:
            _LOGGER.warning("No outlet sections found in response")

    async def set_outlet_state(self, outlet: int, state: bool):
        """Set an outlet's power state."""
        cmd = f"power outlets {outlet} {'on' if state else 'off'} /y"
        await self.send_command(cmd)
        self.outlet_states[outlet] = state

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
        response = await self.send_command("show inlets all details")
        if not response:
            _LOGGER.error("No response when getting sensor values")
            return

        _LOGGER.debug("Sensor values response: %s", response[:200].replace("\r\n", " "))

        try:
            # Extract voltage
            voltage_match = re.search(r"RMS Voltage: (.+?)V", response)
            if voltage_match:
                self.sensors["voltage"] = float(voltage_match.group(1).strip())
                _LOGGER.debug("Found voltage: %f", self.sensors["voltage"])

            # Extract current
            current_match = re.search(r"RMS Current: (.+?)A", response)
            if current_match:
                self.sensors["current"] = float(current_match.group(1).strip())
                _LOGGER.debug("Found current: %f", self.sensors["current"])

            # Extract power
            power_match = re.search(r"Active Power: (.+?)W", response)
            if power_match:
                self.sensors["power"] = float(power_match.group(1).strip())
                _LOGGER.debug("Found power: %f", self.sensors["power"])

            # Extract frequency
            freq_match = re.search(r"Frequency: (.+?)Hz", response)
            if freq_match:
                self.sensors["frequency"] = float(freq_match.group(1).strip())
                _LOGGER.debug("Found frequency: %f", self.sensors["frequency"])

            # Extract power factor
            pf_match = re.search(r"Power Factor: (.+?)%", response)
            if pf_match:
                self.sensors["power_factor"] = float(pf_match.group(1).strip())
                _LOGGER.debug("Found power factor: %f", self.sensors["power_factor"])

            # Extract temperature
            temp_match = re.search(r"Temperature: (.+?)°C", response)
            if temp_match:
                self.sensors["temperature"] = float(temp_match.group(1).strip())
                _LOGGER.debug("Found temperature: %f", self.sensors["temperature"])
            else:
                self.sensors["temperature"] = None
                _LOGGER.debug("No temperature found in response")

        except AttributeError as e:
            _LOGGER.error("Failed to parse sensor values: %s", e)
            _LOGGER.debug("Response causing parse failure: %s", response[:200])
            self._handle_error(f"Failed to parse sensor values: {e}")
        except ValueError as e:
            _LOGGER.error("Failed to convert sensor value: %s", e)
            self._handle_error(f"Failed to convert sensor value: {e}")

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
