import asyncio
import logging
import re
import telnetlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional

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
        _LOGGER.error(error)

    async def connect(self):
        """Connect to the RackLink device."""
        try:
            self.telnet = telnetlib.Telnet(self.host, self.port, timeout=10)
            await self.login()
            await self.get_initial_status()
            self._connected = True
            self._available = True
            self._last_error = None
            self._last_error_time = None
        except Exception as e:
            self._handle_error(f"Connection failed: {e}")
            raise ValueError(f"Connection failed: {e}")

    async def reconnect(self):
        """Reconnect to the device."""
        _LOGGER.info("Attempting to reconnect...")
        await self.disconnect()
        await self.connect()

    async def send_command(self, cmd: str) -> str:
        """Send a command to the device and return the response."""
        if not self.connected:
            await self.connect()

        try:
            self.telnet.write(f"{cmd}\r\n".encode())
            self.last_cmd = cmd
            self.context = cmd.replace(" ", "")
            response = self.telnet.read_until(b"#", timeout=5).decode()
            self.response_cache = response
            return response
        except Exception as e:
            self._handle_error(f"Error sending command: {e}")
            await self.reconnect()
            return await self.send_command(cmd)

    async def login(self):
        """Login to the device."""
        try:
            self.telnet.read_until(b"Username:")
            self.telnet.write(f"{self.username}\r\n".encode())
            self.telnet.read_until(b"Password:")
            self.telnet.write(f"{self.password}\r\n".encode())
            response = self.telnet.read_until(b"#", timeout=5)
            if b"#" not in response:
                raise ValueError("Login failed: Invalid credentials")
        except Exception as e:
            self._handle_error(f"Login failed: {e}")
            raise ValueError(f"Login failed: {e}")

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

    async def disconnect(self):
        """Disconnect from the device."""
        if self.telnet:
            self.telnet.close()
            self._connected = False

    async def periodic_update(self):
        """Perform periodic updates of device status."""
        while True:
            try:
                current_time = asyncio.get_event_loop().time()
                if current_time - self._last_update >= self._update_interval:
                    await self.get_all_outlet_states()
                    await self.get_sensor_values()
                    self._last_update = current_time
                    self._available = True
                    self._last_error = None
                    self._last_error_time = None
            except Exception as e:
                self._handle_error(f"Error during periodic update: {e}")
                await self.reconnect()
            await asyncio.sleep(60)  # Update every 60 seconds

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
