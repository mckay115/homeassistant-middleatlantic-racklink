import asyncio
import logging
import re
import socket
import telnetlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

_LOGGER = logging.getLogger(__name__)


class RacklinkController:
    """Controller for Middle Atlantic RackLink devices."""

    def __init__(self, host: str, port: int, username: str, password: str):
        """Initialize the controller."""
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.telnet: Optional[telnetlib.Telnet] = None
        self.response_cache = ""
        self.last_cmd = ""
        self.context = ""
        self.pdu_name = ""
        self.pdu_firmware = ""
        self.pdu_serial = ""
        self.pdu_mac = ""
        self.pdu_model = ""
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
        self._telnet_timeout = 10  # seconds
        self._connection_task = None
        self._shutdown_requested = False

    @property
    def connected(self) -> bool:
        """Return if we are connected to the device."""
        return self._connected

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

    async def _create_telnet_connection(self) -> Optional[telnetlib.Telnet]:
        """Create a telnet connection in a non-blocking way."""
        try:
            # First check if host is reachable without blocking
            future = asyncio.open_connection(self.host, self.port)
            reader, writer = await asyncio.wait_for(
                future, timeout=self._telnet_timeout
            )
            writer.close()
            await writer.wait_closed()

            # Now create the actual telnet connection in a thread
            return await asyncio.to_thread(
                telnetlib.Telnet, self.host, self.port, timeout=self._telnet_timeout
            )
        except (OSError, asyncio.TimeoutError) as err:
            _LOGGER.error(
                "Failed to create telnet connection to %s:%s: %s",
                self.host,
                self.port,
                err,
            )
            return None

    async def _telnet_read_until(self, pattern: bytes, timeout: int = None) -> bytes:
        """Read from telnet connection in a non-blocking way."""
        if not self.telnet:
            raise ConnectionError("No telnet connection available")

        timeout = timeout or self._telnet_timeout
        try:
            return await asyncio.to_thread(self.telnet.read_until, pattern, timeout)
        except EOFError:
            self._connected = False
            raise ConnectionError("Connection closed by remote host")

    async def _telnet_write(self, data: bytes) -> None:
        """Write to telnet connection in a non-blocking way."""
        if not self.telnet:
            raise ConnectionError("No telnet connection available")

        try:
            await asyncio.to_thread(self.telnet.write, data)
        except EOFError:
            self._connected = False
            raise ConnectionError("Connection closed while writing data")

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
                await self.get_initial_status()
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
                raise ValueError(f"Connection failed: {e}")
            except Exception as e:
                if self.telnet:
                    await asyncio.to_thread(self.telnet.close)
                    self.telnet = None
                self._connected = False
                self._handle_error(f"Unexpected error during connection: {e}")
                raise ValueError(f"Connection failed: {e}")

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

        try:
            _LOGGER.debug("Sending command: %s", cmd)
            await self._telnet_write(f"{cmd}\r\n".encode())
            self.last_cmd = cmd
            self.context = cmd.replace(" ", "")

            response = await asyncio.wait_for(
                self._telnet_read_until(b"#", self._telnet_timeout),
                timeout=self._telnet_timeout * 1.5,  # Add margin to outer timeout
            )

            self.response_cache = response.decode()
            return self.response_cache
        except asyncio.TimeoutError:
            self._handle_error(f"Command timed out: {cmd}")
            # Don't attempt reconnect immediately on timeout, just return empty
            return ""
        except ConnectionError as e:
            self._handle_error(f"Connection error while sending command: {e}")
            await self.reconnect()
            # After reconnection, try sending the command again if we're connected
            if self.connected:
                return await self.send_command(cmd)
            return ""
        except Exception as e:
            self._handle_error(f"Error sending command: {e}")
            await self.reconnect()
            return ""

    async def login(self) -> None:
        """Login to the device."""
        try:
            # Wait for username prompt with timeout
            response = await asyncio.wait_for(
                self._telnet_read_until(b"Username:", self._telnet_timeout),
                timeout=self._telnet_timeout * 1.5,
            )

            await self._telnet_write(f"{self.username}\r\n".encode())

            # Wait for password prompt with timeout
            response = await asyncio.wait_for(
                self._telnet_read_until(b"Password:", self._telnet_timeout),
                timeout=self._telnet_timeout * 1.5,
            )

            await self._telnet_write(f"{self.password}\r\n".encode())

            # Wait for command prompt with timeout
            response = await asyncio.wait_for(
                self._telnet_read_until(b"#", self._telnet_timeout),
                timeout=self._telnet_timeout * 1.5,
            )

            if b"#" not in response:
                raise ValueError("Login failed: Invalid credentials")

        except asyncio.TimeoutError:
            raise ConnectionError("Login timed out")
        except ConnectionError as e:
            raise ConnectionError(f"Login failed: {e}")
        except Exception as e:
            self._handle_error(f"Login failed: {e}")
            raise ValueError(f"Login failed: {e}")

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
            await asyncio.wait_for(self.connect(), timeout=self._telnet_timeout * 2)
        except asyncio.TimeoutError:
            _LOGGER.error(
                "Background connection to %s timed out, will retry on first update",
                self.host,
            )
        except Exception as err:
            _LOGGER.error("Error in background connection to %s: %s", self.host, err)

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
        await self.get_pdu_details()
        await self.get_all_outlet_states()
        await self.get_sensor_values()
        self._last_update = asyncio.get_event_loop().time()

    async def get_pdu_details(self):
        """Get PDU details including name, firmware, and serial number."""
        response = await self.send_command("show pdu details")
        try:
            self.pdu_name = re.search(r"'(.+)'", response).group(1)
            self.pdu_firmware = (
                re.search(r"Firmware Version: (.+)", response).group(1).strip()
            )
            self.pdu_serial = (
                re.search(r"Serial Number: (.+)", response).group(1).strip()
            )
            self.pdu_model = re.search(r"Model: (.+)", response).group(1).strip()
        except AttributeError as e:
            self._handle_error(f"Failed to parse PDU details: {e}")

        response = await self.send_command("show network interface eth1")
        try:
            self.pdu_mac = re.search(r"MAC address: (.+)", response).group(1).strip()
        except AttributeError as e:
            self._handle_error(f"Failed to parse MAC address: {e}")

    async def get_all_outlet_states(self):
        """Get all outlet states and details."""
        response = await self.send_command("show outlets all details")
        pattern = r"Outlet (\d+):\r\n(.*?)Power state: (On|Off).*?RMS Current: (.+)A.*?Active Power: (.+)W.*?Active Energy: (.+)Wh.*?Power Factor: (.+)%"
        for match in re.finditer(pattern, response, re.DOTALL):
            outlet = int(match.group(1))
            name = match.group(2).strip()
            state = match.group(3) == "On"
            current = float(match.group(4))
            power = float(match.group(5))
            energy = float(match.group(6))
            power_factor = float(match.group(7))
            self.outlet_states[outlet] = state
            self.outlet_names[outlet] = name
            self.outlet_power[outlet] = power
            self.outlet_current[outlet] = current
            self.outlet_energy[outlet] = energy
            self.outlet_power_factor[outlet] = power_factor

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
        cmd = f"config"
        await self.send_command(cmd)
        cmd = f'outlet {outlet} name "{name}"'
        await self.send_command(cmd)
        cmd = f"apply"
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
        cmd = f"config"
        await self.send_command(cmd)
        cmd = f'pdu name "{name}"'
        await self.send_command(cmd)
        cmd = f"apply"
        await self.send_command(cmd)
        self.pdu_name = name

    async def get_surge_protection_status(self) -> bool:
        """Get surge protection status."""
        response = await self.send_command("show pdu details")
        match = re.search(r"Surge Protection: (\w+)", response)
        if match:
            return match.group(1) == "Active"
        return False

    async def get_sensor_values(self):
        """Get all sensor values."""
        response = await self.send_command("show inlets all details")
        try:
            self.sensors["voltage"] = float(
                re.search(r"RMS Voltage: (.+)V", response).group(1)
            )
            self.sensors["current"] = float(
                re.search(r"RMS Current: (.+)A", response).group(1)
            )
            self.sensors["power"] = float(
                re.search(r"Active Power: (.+)W", response).group(1)
            )
            self.sensors["frequency"] = float(
                re.search(r"Frequency: (.+)Hz", response).group(1)
            )
            self.sensors["power_factor"] = float(
                re.search(r"Power Factor: (.+)%", response).group(1)
            )

            temp_match = re.search(r"Temperature: (.+)°C", response)
            if temp_match:
                self.sensors["temperature"] = float(temp_match.group(1))
            else:
                self.sensors["temperature"] = None
        except AttributeError as e:
            self._handle_error(f"Failed to parse sensor values: {e}")

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
