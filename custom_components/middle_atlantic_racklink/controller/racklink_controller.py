"""Controller for Middle Atlantic RackLink PDUs."""

import asyncio
import logging
import re
from typing import Dict, Optional, Any

from ..socket_connection import SocketConnection

_LOGGER = logging.getLogger(__name__)


class RacklinkController:
    """Controller class for Middle Atlantic RackLink PDUs."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        timeout: int = 20,
    ) -> None:
        """Initialize the controller.

        Args:
            host: Hostname or IP address of the device
            port: Port number for the connection
            username: Username for authentication
            password: Password for authentication
            timeout: Timeout for socket operations in seconds
        """
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout
        self.socket = SocketConnection(host, port, username, password, timeout)

        # Device information
        self.pdu_name: str = ""
        self.pdu_model: str = ""
        self.pdu_firmware: str = ""
        self.pdu_serial: str = ""
        self.mac_address: str = ""

        # System power data
        self.rms_voltage: float = 0.0
        self.rms_current: float = 0.0
        self.active_power: float = 0.0
        self.active_energy: float = 0.0
        self.line_frequency: float = 0.0

        # Outlet data
        self.outlet_states: Dict[int, bool] = {}
        self.outlet_names: Dict[int, str] = {}

        # Status flags
        self.connected: bool = False
        self.available: bool = False
        self.load_shedding_active: bool = False
        self.sequence_active: bool = False

    async def connect(self) -> bool:
        """Connect to the PDU."""
        try:
            _LOGGER.debug("Connecting to RackLink PDU at %s:%d", self.host, self.port)
            self.connected = await self.socket.connect()
            if self.connected:
                self.available = True
                _LOGGER.info("Successfully connected to RackLink PDU")
            else:
                _LOGGER.error("Failed to connect to RackLink PDU")
                self.available = False
            return self.connected
        except Exception as err:
            _LOGGER.error("Error connecting to RackLink PDU: %s", err)
            self.connected = False
            self.available = False
            return False

    async def disconnect(self) -> None:
        """Disconnect from the PDU."""
        await self.socket.disconnect()
        self.connected = False
        self.available = False

    async def update(self) -> bool:
        """Update PDU data."""
        try:
            if not self.connected:
                if not await self.connect():
                    return False

            # Get PDU details
            await self._update_pdu_details()

            # Get outlet states
            await self._update_outlet_states()

            # Get system status
            await self._update_system_status()

            self.available = True
            return True
        except Exception as err:
            _LOGGER.error("Error updating PDU data: %s", err)
            self.available = False
            return False

    async def _update_pdu_details(self) -> None:
        """Update PDU details."""
        try:
            response = await self.socket.send_command("show pdu details")

            # Parse PDU model
            model_match = re.search(r"Model:\s*(.+?)(?:\r|\n)", response)
            if model_match:
                self.pdu_model = model_match.group(1).strip()

            # Parse firmware version
            fw_match = re.search(r"Firmware Version:\s*(.+?)(?:\r|\n)", response)
            if fw_match:
                self.pdu_firmware = fw_match.group(1).strip()

            # Parse serial number
            sn_match = re.search(r"Serial Number:\s*(.+?)(?:\r|\n)", response)
            if sn_match:
                self.pdu_serial = sn_match.group(1).strip()

            # Parse PDU name
            name_match = re.search(r"Name:\s*'(.+?)'", response)
            if name_match:
                self.pdu_name = name_match.group(1)

            # Get MAC address
            network_response = await self.socket.send_command(
                "show network interface eth1"
            )
            mac_match = re.search(r"MAC address:\s*(.+?)(?:\r|\n)", network_response)
            if mac_match:
                self.mac_address = mac_match.group(1).strip()

        except Exception as err:
            _LOGGER.error("Error updating PDU details: %s", err)
            raise

    async def _update_outlet_states(self) -> None:
        """Update outlet states."""
        try:
            response = await self.socket.send_command("show outlets all")

            # Find all outlets and their states
            outlet_pattern = r"Outlet (\d+):[^\n]*\n\s+(\w+)"
            matches = re.findall(outlet_pattern, response)

            # Update outlets dictionary
            for match in matches:
                outlet_num = int(match[0])
                state = match[1].lower() == "on"
                self.outlet_states[outlet_num] = state

                # Extract the outlet name while we're at it
                name_match = re.search(
                    f"Outlet {outlet_num}: (.*?)$", response, re.MULTILINE
                )
                if name_match:
                    self.outlet_names[outlet_num] = name_match.group(1).strip()
                else:
                    self.outlet_names[outlet_num] = f"Outlet {outlet_num}"

        except Exception as err:
            _LOGGER.error("Error updating outlet states: %s", err)
            raise

    async def _update_system_status(self) -> None:
        """Update system status including power data."""
        try:
            # Get inlet details
            response = await self.socket.send_command("show inlets all details")

            # Parse voltage
            voltage_match = re.search(r"RMS Voltage:\s*([\d.]+)\s*V", response)
            if voltage_match:
                self.rms_voltage = float(voltage_match.group(1))

            # Parse current
            current_match = re.search(r"RMS Current:\s*([\d.]+)\s*A", response)
            if current_match:
                self.rms_current = float(current_match.group(1))

            # Parse power
            power_match = re.search(r"Active Power:\s*([\d.]+)\s*W", response)
            if power_match:
                self.active_power = float(power_match.group(1))

            # Parse energy
            energy_match = re.search(r"Active Energy:\s*([\d.]+)\s*Wh", response)
            if energy_match:
                self.active_energy = float(energy_match.group(1))

            # Parse frequency
            freq_match = re.search(r"Line Frequency:\s*([\d.]+)\s*Hz", response)
            if freq_match:
                self.line_frequency = float(freq_match.group(1))

            # Check load shedding status
            load_shed_response = await self.socket.send_command("show loadshed")
            self.load_shedding_active = "enabled" in load_shed_response.lower()

            # Check sequence status
            sequence_response = await self.socket.send_command("show outlet sequence")
            self.sequence_active = "running" in sequence_response.lower()

        except Exception as err:
            _LOGGER.error("Error updating system status: %s", err)
            raise

    async def turn_outlet_on(self, outlet: int) -> bool:
        """Turn an outlet on."""
        try:
            cmd = f"power outlets {outlet} on /y"
            await self.socket.send_command(cmd)
            self.outlet_states[outlet] = True
            return True
        except Exception as err:
            _LOGGER.error("Error turning outlet %d on: %s", outlet, err)
            return False

    async def turn_outlet_off(self, outlet: int) -> bool:
        """Turn an outlet off."""
        try:
            cmd = f"power outlets {outlet} off /y"
            await self.socket.send_command(cmd)
            self.outlet_states[outlet] = False
            return True
        except Exception as err:
            _LOGGER.error("Error turning outlet %d off: %s", outlet, err)
            return False

    async def cycle_outlet(self, outlet: int) -> bool:
        """Cycle an outlet."""
        try:
            cmd = f"power outlets {outlet} cycle /y"
            await self.socket.send_command(cmd)
            # The outlet will be on after cycling
            self.outlet_states[outlet] = True
            return True
        except Exception as err:
            _LOGGER.error("Error cycling outlet %d: %s", outlet, err)
            return False

    async def cycle_all_outlets(self) -> bool:
        """Cycle all outlets."""
        try:
            cmd = "power outlets all cycle /y"
            await self.socket.send_command(cmd)
            # Update all outlets to on
            for outlet in self.outlet_states:
                self.outlet_states[outlet] = True
            return True
        except Exception as err:
            _LOGGER.error("Error cycling all outlets: %s", err)
            return False

    async def start_load_shedding(self) -> bool:
        """Start load shedding."""
        try:
            cmd = "config\nloadshed enable\napply"
            await self.socket.send_command(cmd)
            self.load_shedding_active = True
            return True
        except Exception as err:
            _LOGGER.error("Error starting load shedding: %s", err)
            return False

    async def stop_load_shedding(self) -> bool:
        """Stop load shedding."""
        try:
            cmd = "config\nloadshed disable\napply"
            await self.socket.send_command(cmd)
            self.load_shedding_active = False
            return True
        except Exception as err:
            _LOGGER.error("Error stopping load shedding: %s", err)
            return False

    async def start_sequence(self) -> bool:
        """Start the outlet sequence."""
        try:
            cmd = "outlet sequence start /y"
            await self.socket.send_command(cmd)
            self.sequence_active = True
            return True
        except Exception as err:
            _LOGGER.error("Error starting sequence: %s", err)
            return False

    async def stop_sequence(self) -> bool:
        """Stop the outlet sequence."""
        try:
            cmd = "outlet sequence stop /y"
            await self.socket.send_command(cmd)
            self.sequence_active = False
            return True
        except Exception as err:
            _LOGGER.error("Error stopping sequence: %s", err)
            return False
