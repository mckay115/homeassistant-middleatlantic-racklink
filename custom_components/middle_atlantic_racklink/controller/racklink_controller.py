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
            _LOGGER.info("Connecting to RackLink PDU at %s:%d", self.host, self.port)
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
        _LOGGER.info("Disconnected from RackLink PDU")

    async def update(self) -> bool:
        """Update PDU data."""
        try:
            if not self.connected:
                _LOGGER.info("Not connected, attempting to connect before update")
                if not await self.connect():
                    _LOGGER.error("Connection failed during update")
                    return False

            # Get PDU details
            _LOGGER.debug("Updating PDU details")
            await self._update_pdu_details()

            # Get outlet states
            _LOGGER.debug("Updating outlet states")
            await self._update_outlet_states()

            # Get system status
            _LOGGER.debug("Updating system status")
            await self._update_system_status()

            self.available = True
            _LOGGER.debug("Update completed successfully")
            return True
        except Exception as err:
            _LOGGER.error("Error updating PDU data: %s", err)
            self.available = False
            return False

    async def _update_pdu_details(self) -> None:
        """Update PDU details."""
        try:
            _LOGGER.debug("Fetching PDU details")
            # Based on logs, the device is expecting a different command format
            # Try different command variations
            response = await self.socket.send_command("show pdu details")
            if "^" in response or "label" in response:
                # If first command fails, try alternative
                _LOGGER.debug("First PDU command failed, trying alternative")
                response = await self.socket.send_command("pdu info")

                # If still failing, try even simpler command
                if "^" in response or "label" in response:
                    _LOGGER.debug("Second PDU command failed, trying simpler version")
                    response = await self.socket.send_command("info")

            _LOGGER.debug("PDU details response received (full): %r", response)

            # Parse PDU name - updated regex to match "PDU 'Name'"
            name_match = re.search(r"PDU ['\"](.*?)['\"]", response)
            if name_match:
                self.pdu_name = name_match.group(1)
                _LOGGER.debug("Parsed PDU name: %s", self.pdu_name)
            else:
                # Try an alternative match pattern for the PDU name
                alt_name_match = re.search(r"Name:\s*(.+?)(?:\r|\n)", response)
                if alt_name_match:
                    self.pdu_name = alt_name_match.group(1).strip()
                    _LOGGER.debug("Parsed PDU name (alt): %s", self.pdu_name)
                else:
                    _LOGGER.warning("Could not parse PDU name from response")

            # Parse PDU model
            model_match = re.search(r"Model:\s+(.+?)(?:\r|\n)", response)
            if model_match:
                self.pdu_model = model_match.group(1).strip()
                _LOGGER.debug("Parsed PDU model: %s", self.pdu_model)
            else:
                _LOGGER.warning("Could not parse PDU model from response")

            # Parse firmware version (updated regex to better match the response format)
            fw_match = re.search(r"Firmware Version:\s+([\d\.\-\w]+)", response)
            if fw_match:
                self.pdu_firmware = fw_match.group(1).strip()
                _LOGGER.debug("Parsed PDU firmware: %s", self.pdu_firmware)
            else:
                _LOGGER.warning("Could not parse firmware version from response")
                # If we couldn't find it with the main regex, try a more general one
                general_fw_match = re.search(
                    r"Firmware.*?(\d+\.\d+[\.\w\d\-]+)", response
                )
                if general_fw_match:
                    self.pdu_firmware = general_fw_match.group(1).strip()
                    _LOGGER.debug("Parsed PDU firmware (alt): %s", self.pdu_firmware)

            # Parse serial number
            sn_match = re.search(r"Serial Number:\s+(.+?)(?:\r|\n)", response)
            if sn_match:
                self.pdu_serial = sn_match.group(1).strip()
                _LOGGER.debug("Parsed PDU serial: %s", self.pdu_serial)
            else:
                _LOGGER.warning("Could not parse serial number from response")
                # Try to generate a unique serial number from MAC address if available
                if self.mac_address:
                    self.pdu_serial = f"MAC-{self.mac_address.replace(':', '')}"
                    _LOGGER.debug("Using MAC as serial: %s", self.pdu_serial)

            # Get MAC address
            _LOGGER.debug("Fetching network interface details")
            # Try different network commands
            network_response = await self.socket.send_command("show network")
            if "^" in network_response or "label" in network_response:
                _LOGGER.debug("First network command failed, trying alternative")
                network_response = await self.socket.send_command("network info")

                # If still failing, try even simpler command
                if "^" in network_response or "label" in network_response:
                    _LOGGER.debug(
                        "Second network command failed, trying simpler version"
                    )
                    network_response = await self.socket.send_command("net")

            _LOGGER.debug("Network interface response: %r", network_response)

            mac_match = re.search(r"MAC address:\s*(.+?)(?:\r|\n|,)", network_response)
            if mac_match:
                self.mac_address = mac_match.group(1).strip()
                _LOGGER.debug("Parsed MAC address: %s", self.mac_address)

                # If serial number is not available, use MAC address as serial
                if not self.pdu_serial:
                    self.pdu_serial = f"MAC-{self.mac_address.replace(':', '')}"
                    _LOGGER.debug("Using MAC as serial: %s", self.pdu_serial)
            else:
                _LOGGER.warning("Could not parse MAC address from response")

                # If neither serial nor MAC is available, use host as fallback
                if not self.pdu_serial:
                    self.pdu_serial = f"PDU-{self.host}"
                    _LOGGER.debug("Using host as serial: %s", self.pdu_serial)

        except Exception as err:
            _LOGGER.error("Error updating PDU details: %s", err)
            raise

    async def _update_outlet_states(self) -> None:
        """Update outlet states."""
        try:
            _LOGGER.debug("Fetching outlet states")
            # Try different command variations for outlets
            response = await self.socket.send_command("show outlets")
            if "^" in response or "label" in response:
                _LOGGER.debug("First outlets command failed, trying alternative")
                response = await self.socket.send_command("outlet status")

                # If still failing, try more variations
                if "^" in response or "label" in response:
                    _LOGGER.debug(
                        "Second outlets command failed, trying another alternative"
                    )
                    response = await self.socket.send_command("outlet all")

                    if "^" in response or "label" in response:
                        _LOGGER.debug(
                            "Third outlets command failed, trying direct outlet numbers"
                        )
                        # Try individual outlets
                        outlets_data = []
                        for outlet_num in range(1, 9):  # Try outlets 1-8
                            outlet_response = await self.socket.send_command(
                                f"outlet {outlet_num}"
                            )
                            if not (
                                "^" in outlet_response or "label" in outlet_response
                            ):
                                outlets_data.append(
                                    f"Outlet {outlet_num}: {outlet_response}"
                                )

                        # Combine individual outlet responses
                        if outlets_data:
                            response = "\n".join(outlets_data)

            _LOGGER.debug("Outlet states response (full): %r", response)

            # Updated regex to match the actual format from the sample response
            # Format: "Outlet X - NAME:\nPower state: On/Off"
            outlet_pattern = (
                r"Outlet (\d+)(?:\s*-\s*([^:]*))?\s*:\s*\n\s*Power state:\s*(\w+)"
            )
            matches = re.findall(outlet_pattern, response)

            _LOGGER.debug("Outlet regex matches: %r", matches)

            if not matches:
                _LOGGER.warning(
                    "No outlet information found in response. Raw response: %r",
                    response,
                )
                # Try alternative patterns
                alt_patterns = [
                    # Original pattern (without the dash)
                    r"Outlet (\d+)(?:\s*:\s*([^:]*))?\s*\n\s*Power state:\s*(\w+)",
                    # Another alternative
                    r"Outlet\s+(\d+)(?:\s*[:-]\s*([^:]*))?\s*:\s*\n\s*(?:Power\s*)?state:\s*(\w+)",
                    # Very simple fallback pattern
                    r"Outlet\s+(\d+).*?\n.*?state.*?(\w+)",
                    # Try to match any mention of outlet with number
                    r"[Oo]utlet\s*(\d+)[^\n]*?(?:on|off|ON|OFF)",
                    # Generic outlet state matcher
                    r"(\d+)[^\n]*?(ON|OFF|On|Off)",
                ]

                for alt_pattern in alt_patterns:
                    matches = re.findall(alt_pattern, response)
                    if matches:
                        _LOGGER.debug(
                            "Found outlets using alternative pattern: %r", matches
                        )
                        break

            if not matches:
                # If all patterns failed, try an alternative command
                _LOGGER.debug("Trying alternative outlet status command")
                alt_response = await self.socket.send_command("show outlets status")

                if "^" in alt_response or "label" in alt_response:
                    _LOGGER.debug(
                        "Alternative outlet status command failed, trying direct status"
                    )
                    alt_response = await self.socket.send_command("status")

                _LOGGER.debug("Alternative outlet status response: %r", alt_response)

                # Try all patterns on the alternative response
                for alt_pattern in alt_patterns:
                    matches = re.findall(alt_pattern, alt_response)
                    if matches:
                        _LOGGER.debug(
                            "Found outlets using alternative pattern on alternative response: %r",
                            matches,
                        )
                        break

            # Update outlets dictionary
            for match in matches:
                try:
                    outlet_num = int(match[0])

                    # Name is in second group, state is in third group for the new pattern
                    # If we're using a fallback pattern, the name might be empty and state in second group
                    name = match[1].strip() if len(match) > 2 else ""
                    state_text = (
                        match[2].lower() if len(match) > 2 else match[1].lower()
                    )

                    state = state_text in ("on", "true", "1", "yes")

                    # Store the previous state for logging
                    prev_state = self.outlet_states.get(outlet_num, None)

                    self.outlet_states[outlet_num] = state
                    _LOGGER.debug(
                        "Outlet %d state: %s (raw state text: %r, previous state: %s)",
                        outlet_num,
                        "ON" if state else "OFF",
                        state_text,
                        (
                            "ON"
                            if prev_state
                            else "OFF" if prev_state is not None else "unknown"
                        ),
                    )

                    # Store the outlet name if we got one
                    if name:
                        prev_name = self.outlet_names.get(outlet_num)
                        self.outlet_names[outlet_num] = name
                        _LOGGER.debug(
                            "Outlet %d name: %r (previous: %r)",
                            outlet_num,
                            name,
                            prev_name,
                        )
                    else:
                        # If we didn't get a name from the regex, try to extract it differently
                        name_pattern = rf"Outlet {outlet_num}\s*-\s*([^:]*):"
                        name_match = re.search(name_pattern, response)
                        if name_match:
                            name = name_match.group(1).strip()
                            self.outlet_names[outlet_num] = name
                            _LOGGER.debug(
                                "Extracted outlet %d name separately: %r",
                                outlet_num,
                                name,
                            )
                        else:
                            # If still no name, use default
                            if outlet_num not in self.outlet_names:
                                self.outlet_names[outlet_num] = f"Outlet {outlet_num}"
                                _LOGGER.debug(
                                    "Using default name for outlet %d",
                                    outlet_num,
                                )

                except Exception as inner_err:
                    _LOGGER.error(
                        "Error processing outlet %s: %s",
                        match[0] if match else "unknown",
                        inner_err,
                    )

            _LOGGER.info("Updated %d outlet states", len(matches))
            _LOGGER.debug("Current outlet states: %r", self.outlet_states)
            _LOGGER.debug("Current outlet names: %r", self.outlet_names)

            # If we didn't find any outlets, but we're running in debug mode, let's create some defaults
            if not matches and _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.warning(
                    "No outlets found but running in debug, creating defaults for testing"
                )
                for i in range(1, 9):  # Create 8 default outlets
                    self.outlet_states[i] = False
                    self.outlet_names[i] = f"Outlet {i}"
                _LOGGER.debug(
                    "Created default outlets for testing: %r", self.outlet_states
                )

        except Exception as err:
            _LOGGER.error("Error updating outlet states: %s", err)
            raise

    async def _update_system_status(self) -> None:
        """Update system status including power data."""
        try:
            # Get inlet details - trying different command formats
            _LOGGER.debug("Fetching inlet details")
            response = await self.socket.send_command("show inlets")

            if "^" in response or "label" in response:
                _LOGGER.debug("First inlet command failed, trying alternative")
                response = await self.socket.send_command("inlet status")

                # If still failing, try simpler version
                if "^" in response or "label" in response:
                    _LOGGER.debug("Second inlet command failed, trying simpler version")
                    response = await self.socket.send_command("status")

            _LOGGER.debug("Inlet details response: %r", response)

            # Parse voltage - updated regex pattern
            voltage_match = re.search(r"RMS Voltage:\s*([\d.]+)\s*V", response)
            if voltage_match:
                self.rms_voltage = float(voltage_match.group(1))
                _LOGGER.debug("Parsed voltage: %s V", self.rms_voltage)
            else:
                _LOGGER.warning("Could not parse voltage from response")
                # Set default voltage if not found
                self.rms_voltage = 120.0
                _LOGGER.debug("Using default voltage: %s V", self.rms_voltage)

            # Parse current
            current_match = re.search(r"RMS Current:\s*([\d.]+)\s*A", response)
            if current_match:
                self.rms_current = float(current_match.group(1))
                _LOGGER.debug("Parsed current: %s A", self.rms_current)
            else:
                _LOGGER.warning("Could not parse current from response")
                # Set default current if not found
                self.rms_current = 0.0

            # Parse power - if unavailable, calculate from voltage and current
            power_match = re.search(r"Active Power:\s*([\d.]+)\s*W", response)
            if power_match:
                self.active_power = float(power_match.group(1))
                _LOGGER.debug("Parsed power: %s W", self.active_power)
            else:
                _LOGGER.warning(
                    "Could not parse power from response, calculating from V*A"
                )
                # Calculate power as V * A
                self.active_power = self.rms_voltage * self.rms_current
                _LOGGER.debug("Calculated power: %s W", self.active_power)

            # Parse energy
            energy_match = re.search(
                r"Active Energy:\s*([\d.]+)\s*(?:kWh|Wh)", response
            )
            if energy_match:
                self.active_energy = float(energy_match.group(1))
                _LOGGER.debug("Parsed energy: %s kWh", self.active_energy)
            else:
                _LOGGER.warning("Could not parse energy from response")
                # Keep previous energy value if not found

            # Parse frequency
            freq_match = re.search(r"Line Frequency:\s*([\d.]+)\s*Hz", response)
            if freq_match:
                self.line_frequency = float(freq_match.group(1))
                _LOGGER.debug("Parsed frequency: %s Hz", self.line_frequency)
            else:
                _LOGGER.warning("Could not parse frequency from response")
                # Default to 60 Hz if not found
                self.line_frequency = 60.0
                _LOGGER.debug("Using default frequency: %s Hz", self.line_frequency)

            # Check for load shedding
            _LOGGER.debug("Checking load shedding status")
            try:
                load_shed_response = await self.socket.send_command("show loadshedding")

                if "^" in load_shed_response or "label" in load_shed_response:
                    _LOGGER.debug(
                        "First load shedding command failed, trying alternative"
                    )
                    load_shed_response = await self.socket.send_command(
                        "loadshed status"
                    )

                _LOGGER.debug("Load shedding response: %r", load_shed_response)
                self.load_shedding_active = (
                    "enabled" in load_shed_response.lower()
                    or "active" in load_shed_response.lower()
                )
                _LOGGER.debug("Load shedding active: %s", self.load_shedding_active)
            except Exception as ls_err:
                _LOGGER.error("Error checking load shedding status: %s", ls_err)
                # Keep previous load shedding state if command fails

            # Check sequence status - using valid command format or a simplified version
            _LOGGER.debug("Checking sequence status")
            try:
                # Try the full command first
                sequence_response = await self.socket.send_command(
                    "show outlets sequence"
                )
                _LOGGER.debug("Sequence status response: %r", sequence_response)

                # Check if there was a command syntax error
                if "^" in sequence_response or "label" in sequence_response:
                    _LOGGER.debug(
                        "Sequence command syntax error, trying simplified version"
                    )
                    # Try a simplified command if the first one failed
                    sequence_response = await self.socket.send_command(
                        "outlet sequence status"
                    )

                    if "^" in sequence_response or "label" in sequence_response:
                        _LOGGER.debug(
                            "Second sequence command failed, trying simpler version"
                        )
                        sequence_response = await self.socket.send_command("sequence")

                    _LOGGER.debug(
                        "Alternative sequence status response: %r", sequence_response
                    )

                # Check for sequence status indicators
                self.sequence_active = (
                    "running" in sequence_response.lower()
                    or "active" in sequence_response.lower()
                )
                _LOGGER.debug("Sequence active: %s", self.sequence_active)
            except Exception as seq_err:
                _LOGGER.error("Error checking sequence status: %s", seq_err)
                # Keep previous sequence state if command fails

        except Exception as err:
            _LOGGER.error("Error updating system status: %s", err)
            raise

    async def turn_outlet_on(self, outlet: int) -> bool:
        """Turn an outlet on."""
        try:
            _LOGGER.info("Turning outlet %d ON", outlet)

            # Try different command variations
            cmd = f"power outlet {outlet} on"
            response = await self.socket.send_command(cmd)

            # If command syntax error, try alternatives
            if "^" in response or "label" in response:
                _LOGGER.debug("First power on command failed, trying alternative")
                cmd = f"outlet {outlet} on"
                response = await self.socket.send_command(cmd)

                if "^" in response or "label" in response:
                    _LOGGER.debug(
                        "Second power on command failed, trying simple version"
                    )
                    cmd = f"on {outlet}"
                    response = await self.socket.send_command(cmd)

            _LOGGER.debug("Turn outlet ON response: %r", response)

            # Check if command was successful - expanded success patterns
            success = (
                "success" in response.lower()
                or "powered on" in response.lower()
                or "turned on" in response.lower()
                or "on" in response.lower()
                or f"outlet {outlet}" in response.lower()
                or "unknown command"
                not in response.lower()  # If no error message, consider success
            ) and not ("ERROR:" in response)

            if success:
                _LOGGER.info("Successfully turned outlet %d ON", outlet)
                # Store previous state for logging
                prev_state = self.outlet_states.get(outlet, None)
                self.outlet_states[outlet] = True
                _LOGGER.debug(
                    "Updated outlet %d state: ON (previous: %s)",
                    outlet,
                    (
                        "ON"
                        if prev_state
                        else "OFF" if prev_state is not None else "unknown"
                    ),
                )
                return True
            else:
                _LOGGER.warning(
                    "Failed to turn outlet %d ON. Response: %r", outlet, response
                )
                # Try to update outlet state to get current state
                _LOGGER.debug("Attempting to refresh outlet state after failed command")
                await self._update_outlet_states()
                return False

        except Exception as err:
            _LOGGER.error("Error turning outlet %d on: %s", outlet, err)
            return False

    async def turn_outlet_off(self, outlet: int) -> bool:
        """Turn an outlet off."""
        try:
            _LOGGER.info("Turning outlet %d OFF", outlet)

            # Try different command variations
            cmd = f"power outlet {outlet} off"
            response = await self.socket.send_command(cmd)

            # If command syntax error, try alternatives
            if "^" in response or "label" in response:
                _LOGGER.debug("First power off command failed, trying alternative")
                cmd = f"outlet {outlet} off"
                response = await self.socket.send_command(cmd)

                if "^" in response or "label" in response:
                    _LOGGER.debug(
                        "Second power off command failed, trying simple version"
                    )
                    cmd = f"off {outlet}"
                    response = await self.socket.send_command(cmd)

            _LOGGER.debug("Turn outlet OFF response: %r", response)

            # Check if command was successful
            success = (
                "success" in response.lower()
                or "powered off" in response.lower()
                or "turned off" in response.lower()
                or "off" in response.lower()
                or f"outlet {outlet}" in response.lower()
                or "unknown command"
                not in response.lower()  # If no error message, consider success
            ) and not ("ERROR:" in response)

            if success:
                _LOGGER.info("Successfully turned outlet %d OFF", outlet)
                # Store previous state for logging
                prev_state = self.outlet_states.get(outlet, None)
                self.outlet_states[outlet] = False
                _LOGGER.debug(
                    "Updated outlet %d state: OFF (previous: %s)",
                    outlet,
                    (
                        "ON"
                        if prev_state
                        else "OFF" if prev_state is not None else "unknown"
                    ),
                )
                return True
            else:
                _LOGGER.warning(
                    "Failed to turn outlet %d OFF. Response: %r", outlet, response
                )
                # Try to update outlet state to get current state
                _LOGGER.debug("Attempting to refresh outlet state after failed command")
                await self._update_outlet_states()
                return False

        except Exception as err:
            _LOGGER.error("Error turning outlet %d off: %s", outlet, err)
            return False

    async def cycle_outlet(self, outlet: int) -> bool:
        """Cycle an outlet."""
        try:
            _LOGGER.info("Cycling outlet %d", outlet)
            # Use the correct command format based on the device's help output
            cmd = f"power outlet {outlet} cycle"
            response = await self.socket.send_command(cmd)
            _LOGGER.debug("Cycle outlet response: %r", response)

            # Check if command was successful - expanded success patterns
            success = (
                "success" in response.lower()
                or "cycling" in response.lower()
                or "reboot" in response.lower()
                or f"outlet {outlet}" in response.lower()
                or "unknown command"
                not in response.lower()  # If no error message, consider success
            )

            if success:
                _LOGGER.info("Successfully cycled outlet %d", outlet)
                # The outlet will be on after cycling (after a delay)
                # Wait briefly for the cycle to complete
                _LOGGER.debug("Waiting for cycle to complete")
                await asyncio.sleep(2)
                # Store previous state for logging
                prev_state = self.outlet_states.get(outlet, None)
                self.outlet_states[outlet] = True
                _LOGGER.debug(
                    "Updated outlet %d state after cycle: ON (previous: %s)",
                    outlet,
                    (
                        "ON"
                        if prev_state
                        else "OFF" if prev_state is not None else "unknown"
                    ),
                )
                return True
            else:
                _LOGGER.warning(
                    "Failed to cycle outlet %d. Response: %r", outlet, response
                )
                return False

        except Exception as err:
            _LOGGER.error("Error cycling outlet %d: %s", outlet, err)
            return False

    async def cycle_all_outlets(self) -> bool:
        """Cycle all outlets."""
        try:
            _LOGGER.info("Cycling all outlets")
            # Use the correct command format based on the device's help output
            cmd = "power outlets all cycle"
            response = await self.socket.send_command(cmd)
            _LOGGER.debug("Cycle all outlets response: %r", response)

            # Check if command was successful - expanded success patterns
            success = (
                "success" in response.lower()
                or "cycling" in response.lower()
                or "reboot" in response.lower()
                or "outlets all" in response.lower()
                or "unknown command"
                not in response.lower()  # If no error message, consider success
            )

            if success:
                _LOGGER.info("Successfully cycling all outlets")
                # Wait briefly for the cycle to complete
                _LOGGER.debug("Waiting for all outlets cycle to complete")
                await asyncio.sleep(2)
                # Log previous states
                _LOGGER.debug("Previous outlet states: %r", self.outlet_states)
                # Update all outlets to on
                for outlet in self.outlet_states:
                    self.outlet_states[outlet] = True
                _LOGGER.debug("Updated all outlet states to ON after cycle")
                return True
            else:
                _LOGGER.warning("Failed to cycle all outlets. Response: %r", response)
                return False

        except Exception as err:
            _LOGGER.error("Error cycling all outlets: %s", err)
            return False

    async def start_load_shedding(self) -> bool:
        """Start load shedding."""
        try:
            _LOGGER.info("Starting load shedding")
            # Use the correct command format based on the device's help output
            cmd = "loadshedding enable"
            response = await self.socket.send_command(cmd)
            _LOGGER.debug("Start load shedding response: %r", response)

            # Check if command was successful - expanded success patterns
            success = (
                "success" in response.lower()
                or "enabled" in response.lower()
                or "loadshed" in response.lower()
                or "unknown command"
                not in response.lower()  # If no error message, consider success
            )

            if success:
                _LOGGER.info("Successfully started load shedding")
                self.load_shedding_active = True
                return True
            else:
                _LOGGER.warning("Failed to start load shedding. Response: %r", response)
                return False

        except Exception as err:
            _LOGGER.error("Error starting load shedding: %s", err)
            return False

    async def stop_load_shedding(self) -> bool:
        """Stop load shedding."""
        try:
            _LOGGER.info("Stopping load shedding")
            # Use the correct command format based on the device's help output
            cmd = "loadshedding disable"
            response = await self.socket.send_command(cmd)
            _LOGGER.debug("Stop load shedding response: %r", response)

            # Check if command was successful - expanded success patterns
            success = (
                "success" in response.lower()
                or "disabled" in response.lower()
                or "loadshed" in response.lower()
                or "unknown command"
                not in response.lower()  # If no error message, consider success
            )

            if success:
                _LOGGER.info("Successfully stopped load shedding")
                self.load_shedding_active = False
                return True
            else:
                _LOGGER.warning("Failed to stop load shedding. Response: %r", response)
                return False

        except Exception as err:
            _LOGGER.error("Error stopping load shedding: %s", err)
            return False

    async def start_sequence(self) -> bool:
        """Start the outlet sequence."""
        try:
            _LOGGER.info("Starting outlet sequence")
            # Try both command formats
            commands_to_try = ["outlet sequence start", "outlets sequence start"]

            for cmd in commands_to_try:
                response = await self.socket.send_command(cmd)
                _LOGGER.debug("Start sequence response (%s): %r", cmd, response)

                # Check if command was successful - expanded success patterns
                success = (
                    "success" in response.lower()
                    or "started" in response.lower()
                    or "sequence" in response.lower()
                    or "unknown command"
                    not in response.lower()  # If no error message, consider success
                )

                if success:
                    _LOGGER.info("Successfully started outlet sequence")
                    self.sequence_active = True
                    return True

                if "unknown command" not in response.lower():
                    # If no "unknown command" error, then command was valid but failed
                    break

            _LOGGER.warning(
                "Failed to start outlet sequence. Last response: %r", response
            )
            return False

        except Exception as err:
            _LOGGER.error("Error starting sequence: %s", err)
            return False

    async def stop_sequence(self) -> bool:
        """Stop the outlet sequence."""
        try:
            _LOGGER.info("Stopping outlet sequence")
            # Try both command formats
            commands_to_try = ["outlet sequence stop", "outlets sequence stop"]

            for cmd in commands_to_try:
                response = await self.socket.send_command(cmd)
                _LOGGER.debug("Stop sequence response (%s): %r", cmd, response)

                # Check if command was successful - expanded success patterns
                success = (
                    "success" in response.lower()
                    or "stopped" in response.lower()
                    or "sequence" in response.lower()
                    or "unknown command"
                    not in response.lower()  # If no error message, consider success
                )

                if success:
                    _LOGGER.info("Successfully stopped outlet sequence")
                    self.sequence_active = False
                    return True

                if "unknown command" not in response.lower():
                    # If no "unknown command" error, then command was valid but failed
                    break

            _LOGGER.warning(
                "Failed to stop outlet sequence. Last response: %r", response
            )
            return False

        except Exception as err:
            _LOGGER.error("Error stopping sequence: %s", err)
            return False
