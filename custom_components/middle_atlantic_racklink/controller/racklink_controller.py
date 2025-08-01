"""Controller for Middle Atlantic RackLink PDUs."""

# Standard library imports
import asyncio
import logging
import re
from typing import Any, Dict, Optional

# Local application/library specific imports
from ..socket_connection import (
    SocketConnection,
    SocketConfig,
    OUTLET_ON,
    OUTLET_OFF,
    OUTLET_CYCLE,
)

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

        # Create SocketConfig
        config = SocketConfig(
            host=host,
            port=port,
            username=username,
            password=password,
            timeout=timeout,
        )
        self.socket = SocketConnection(config)

        # Device information
        self.pdu_name: str = f"RackLink PDU {host}"
        self.pdu_model: str = "RackLink"
        self.pdu_firmware: str = "Unknown"
        self.pdu_serial: str = f"PDU-{host.replace('.', '-')}"  # Default fallback
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
        self.outlet_power_data: Dict[int, float] = (
            {}
        )  # Individual outlet power consumption
        self.outlet_non_critical: Dict[int, bool] = {}  # Non-critical outlet flags

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
            _LOGGER.info("Fetching PDU details")

            # Use exact command syntax from working response samples
            response = await self.socket.send_command("show pdu details")
            if "^" in response or "label" in response:
                _LOGGER.warning(
                    "🔍 PDU DETAILS command failed with syntax error - session may be corrupted"
                )
            else:
                _LOGGER.debug(
                    "🔍 PDU DETAILS response received (%d chars)", len(response)
                )

            # Add delay between commands to prevent session corruption
            await asyncio.sleep(0.5)

            # Parse PDU name - exact format from response samples: PDU 'LiskoLabs Rack'
            name_match = re.search(r"PDU ['\"](.*?)['\"]", response)
            if name_match:
                self.pdu_name = name_match.group(1)
                _LOGGER.debug("✅ Parsed PDU name: %s", self.pdu_name)
            else:
                _LOGGER.warning("❌ Could not parse PDU name from response")

            # Parse PDU model - exact format: Model:            RLNK-P920R
            model_match = re.search(r"Model:\s*(.+?)(?:\r|\n)", response)
            if model_match:
                self.pdu_model = model_match.group(1).strip()
                _LOGGER.debug("✅ Parsed PDU model: %s", self.pdu_model)
            else:
                _LOGGER.warning("❌ Could not parse PDU model from response")

            # Parse firmware version - exact format: Firmware Version: 2.2.0.1-51126
            fw_match = re.search(r"Firmware Version:\s*(.+?)(?:\r|\n)", response)
            if fw_match:
                self.pdu_firmware = fw_match.group(1).strip()
                _LOGGER.debug("✅ Parsed PDU firmware: %s", self.pdu_firmware)
            else:
                _LOGGER.warning("❌ Could not parse firmware version from response")

            # Parse serial number - exact format: Serial Number:    RLNKP-920_050a82
            sn_match = re.search(r"Serial Number:\s*(.+?)(?:\r|\n)", response)
            if sn_match:
                self.pdu_serial = sn_match.group(1).strip()
                _LOGGER.debug("✅ Parsed PDU serial: %s", self.pdu_serial)
            else:
                _LOGGER.warning("❌ Could not parse serial number from response")
                # Generate a fallback serial number to ensure we always have one
                if not self.pdu_serial or self.pdu_serial.startswith("PDU-"):
                    if self.mac_address:
                        self.pdu_serial = f"MAC-{self.mac_address.replace(':', '')}"
                        _LOGGER.debug("Using MAC as serial: %s", self.pdu_serial)
                    else:
                        # Use host-based fallback if no MAC available yet
                        self.pdu_serial = f"PDU-{self.host.replace('.', '-')}"
                        _LOGGER.debug("Using host-based serial: %s", self.pdu_serial)

            # Get MAC address using exact command syntax
            _LOGGER.debug("Fetching network interface details")
            await asyncio.sleep(0.5)  # Prevent rapid commands
            network_response = await self.socket.send_command(
                "show network interface eth1"
            )
            _LOGGER.debug("Network interface response: %s", network_response[:200])
            await asyncio.sleep(0.5)  # Prevent rapid commands

            # Parse MAC address - exact format: MAC address:               00:1e:c5:05:0a:82
            mac_match = re.search(r"MAC address:\s*(.+?)(?:\r|\n|,)", network_response)
            if mac_match:
                self.mac_address = mac_match.group(1).strip()
                _LOGGER.debug("✅ Parsed MAC address: %s", self.mac_address)

                # If serial number is still fallback, update with MAC
                if self.pdu_serial.startswith("PDU-"):
                    self.pdu_serial = f"MAC-{self.mac_address.replace(':', '')}"
                    _LOGGER.debug("Updated serial to use MAC: %s", self.pdu_serial)
            else:
                _LOGGER.warning("❌ Could not parse MAC address from response")

                # If neither serial nor MAC is available, use host as fallback
                if not self.pdu_serial:
                    self.pdu_serial = f"PDU-{self.host}"
                    _LOGGER.debug("Using host as serial: %s", self.pdu_serial)

        except Exception as err:
            _LOGGER.error("Error updating PDU details: %s", err)
            raise

    async def _update_outlet_states(self) -> None:
        """Update outlet states using appropriate protocol."""
        try:
            # Check protocol type and use appropriate method
            if (
                hasattr(self.socket, "_protocol_type")
                and self.socket._protocol_type == "telnet"
            ):
                _LOGGER.debug("Fetching outlet states using Telnet protocol")
                await asyncio.sleep(0.5)  # Prevent rapid commands
                outlet_data = await self.socket.telnet_read_outlet_states()

                if outlet_data:
                    _LOGGER.info("Found %d outlet states via Telnet", len(outlet_data))
                    for outlet_num, state in outlet_data.items():
                        self.outlet_states[outlet_num] = state

                    # Get outlet names from the socket connection if available
                    if (
                        hasattr(self.socket, "_outlet_names")
                        and self.socket._outlet_names
                    ):
                        self.outlet_names.update(self.socket._outlet_names)
                        _LOGGER.debug("✅ Updated outlet names: %s", self.outlet_names)
                    else:
                        # Set default names if not parsed from response
                        for outlet_num in outlet_data.keys():
                            if outlet_num not in self.outlet_names:
                                self.outlet_names[outlet_num] = f"Outlet {outlet_num}"
                else:
                    _LOGGER.warning("No outlet states found via Telnet")

            else:
                # Use binary protocol
                _LOGGER.debug("Fetching outlet states using binary protocol")

                # If we don't know how many outlets we have, try a reasonable range
                if not self.outlet_states:
                    # Try outlets 1-16 to discover available outlets
                    outlet_range = range(1, 17)
                else:
                    # Use known outlets
                    outlet_range = self.outlet_states.keys()

                outlet_data = {}
                for outlet_num in outlet_range:
                    state = await self.socket.read_outlet_state(outlet_num)
                    if state is not None:  # Valid response
                        outlet_data[outlet_num] = state
                        _LOGGER.debug(
                            "Outlet %d state: %s",
                            outlet_num,
                            "ON" if state else "OFF",
                        )
                    else:
                        # If we're discovering outlets and get no response, we've found the limit
                        if not self.outlet_states:
                            break

                # Update our state with discovered outlets
                if outlet_data:
                    _LOGGER.info("Found %d outlet states via binary", len(outlet_data))
                    for outlet_num, state in outlet_data.items():
                        self.outlet_states[outlet_num] = state
                        # Set default name if not already set
                        if outlet_num not in self.outlet_names:
                            self.outlet_names[outlet_num] = f"Outlet {outlet_num}"
                else:
                    _LOGGER.warning("No outlet states found via binary")

        except Exception as err:
            _LOGGER.error("Error updating outlet states: %s", err)
            raise

    async def _update_system_status(self) -> None:
        """Update system status including power data - try efficient system commands first."""
        try:
            # Try efficient system-wide power commands first
            system_data = await self._try_system_power_commands()

            if system_data:
                # Use system-wide data - much faster!
                self.active_power = system_data.get("power", 0.0)
                self.rms_current = system_data.get("current", 0.0)
                self.rms_voltage = system_data.get("voltage", 120.0)
                self.active_energy = system_data.get("energy", 0.0)
                self.line_frequency = system_data.get("frequency", 60.0)

                _LOGGER.info(
                    "📊 PDU System Status: %.1f W, %.3f A, %.1f V, %.1f Wh, %.1f Hz (from system command)",
                    self.active_power,
                    self.rms_current,
                    self.rms_voltage,
                    self.active_energy,
                    self.line_frequency,
                )

                # Still need to get non-critical flags from individual outlets (less critical, can be async)
                await self._update_outlet_metadata()

            else:
                # Fallback to individual outlet polling if system commands fail
                _LOGGER.warning(
                    "System power commands failed, falling back to individual outlet polling"
                )
                await self._update_system_status_fallback()

            # Set default values for advanced features (not essential for basic operation)
            self.load_shedding_active = False
            self.sequence_active = False
            _LOGGER.debug(
                "Skipping advanced status checks to prevent session corruption"
            )

        except Exception as err:
            _LOGGER.error("Error updating system status: %s", err)
            raise

    async def turn_outlet_on(self, outlet: int) -> bool:
        """Turn an outlet on using appropriate protocol."""
        try:
            _LOGGER.info("Turning outlet %d ON", outlet)

            # Check protocol type and use appropriate method
            if (
                hasattr(self.socket, "_protocol_type")
                and self.socket._protocol_type == "telnet"
            ):
                success = await self.socket.telnet_outlet_command(outlet, "on")
            else:
                # Use binary protocol command
                success = await self.socket.send_outlet_command(outlet, OUTLET_ON)

            if success:
                _LOGGER.info("Successfully turned outlet %d ON", outlet)
                self.outlet_states[outlet] = True
                return True
            else:
                _LOGGER.warning("Failed to turn outlet %d ON", outlet)
                return False

        except Exception as err:
            _LOGGER.error("Error turning outlet %d on: %s", outlet, err)
            return False

    async def turn_outlet_off(self, outlet: int) -> bool:
        """Turn an outlet off using appropriate protocol."""
        try:
            _LOGGER.info("Turning outlet %d OFF", outlet)

            # Check protocol type and use appropriate method
            if (
                hasattr(self.socket, "_protocol_type")
                and self.socket._protocol_type == "telnet"
            ):
                success = await self.socket.telnet_outlet_command(outlet, "off")
            else:
                # Use binary protocol command
                success = await self.socket.send_outlet_command(outlet, OUTLET_OFF)

            if success:
                _LOGGER.info("Successfully turned outlet %d OFF", outlet)
                self.outlet_states[outlet] = False
                return True
            else:
                _LOGGER.warning("Failed to turn outlet %d OFF", outlet)
                return False

        except Exception as err:
            _LOGGER.error("Error turning outlet %d off: %s", outlet, err)
            return False

    async def cycle_outlet(self, outlet: int, cycle_time: int = 5) -> bool:
        """Cycle an outlet using appropriate protocol."""
        try:
            _LOGGER.info("Cycling outlet %d for %d seconds", outlet, cycle_time)

            # Check protocol type and use appropriate method
            if (
                hasattr(self.socket, "_protocol_type")
                and self.socket._protocol_type == "telnet"
            ):
                success = await self.socket.telnet_outlet_command(outlet, "cycle")
            else:
                # Use binary protocol command with cycle time
                success = await self.socket.send_outlet_command(
                    outlet, OUTLET_CYCLE, cycle_time
                )

            if success:
                _LOGGER.info("Successfully cycled outlet %d", outlet)
                # The outlet will be on after cycling (after a delay)
                # Wait briefly for the cycle to complete
                _LOGGER.debug("Waiting for cycle to complete")
                await asyncio.sleep(cycle_time + 1)  # Wait for cycle time plus buffer
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
                _LOGGER.warning("Failed to cycle outlet %d", outlet)
                return False

        except Exception as err:
            _LOGGER.error("Error cycling outlet %d: %s", outlet, err)
            return False

    async def cycle_all_outlets(self, cycle_time: int = 5) -> bool:
        """Cycle all outlets using binary protocol."""
        try:
            _LOGGER.info(
                "Cycling all outlets (%d outlets) for %d seconds",
                len(self.outlet_states),
                cycle_time,
            )

            success_count = 0
            total_outlets = len(self.outlet_states)

            # Cycle each outlet individually
            for outlet_num in self.outlet_states:
                outlet_success = await self.socket.send_outlet_command(
                    outlet_num, OUTLET_CYCLE, cycle_time
                )
                if outlet_success:
                    success_count += 1
                    _LOGGER.debug("Successfully cycled outlet %d", outlet_num)
                else:
                    _LOGGER.warning("Failed to cycle outlet %d", outlet_num)

            if success_count > 0:
                _LOGGER.info(
                    "Successfully cycled %d of %d outlets", success_count, total_outlets
                )
                # Wait for the cycle to complete
                _LOGGER.debug("Waiting for all outlet cycles to complete")
                await asyncio.sleep(cycle_time + 1)
                # Update all successfully cycled outlets to on
                for outlet_num in self.outlet_states:
                    self.outlet_states[outlet_num] = True
                _LOGGER.debug("Updated all outlet states to ON after cycle")
                return success_count == total_outlets
            else:
                _LOGGER.warning("Failed to cycle any outlets")
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

    def get_model_capabilities(self) -> Dict[str, Any]:
        """Return model capabilities based on available data.

        Returns:
            Dict containing model capabilities like number of outlets, features, etc.
        """
        capabilities = {
            "num_outlets": (
                max(len(self.outlet_states), 8) if self.outlet_states else 8
            ),  # Use actual count or default to 8
            "has_surge_protection": True,  # Most RackLink devices have surge protection
            "has_current_monitoring": True,
            "has_power_monitoring": True,
            "has_energy_monitoring": True,
            "supports_outlet_control": True,
        }

        # Adjust outlet count based on actual data
        if self.outlet_states:
            capabilities["num_outlets"] = max(self.outlet_states.keys())

        _LOGGER.debug("Controller capabilities: %s", capabilities)
        return capabilities

    async def get_surge_protection_status(self) -> bool:
        """Get surge protection status.

        Returns:
            bool: True if surge protection is active/OK, False if failed
        """
        # Most RackLink devices have surge protection that's typically active
        # This would need specific parsing if the device provides surge status
        return True  # Default to True for now

    async def _try_system_power_commands(self) -> Dict[str, float]:
        """Try efficient system-wide power commands to get total PDU consumption.

        Returns:
            Dict with power data if successful, empty dict if all commands fail
        """
        # Commands to try for system-wide power data (in order of preference)
        commands_to_try = [
            "show pdu power",  # Direct PDU power command
            "show inlets all details",  # Inlet power data
            "power status",  # Simple power status
            "show power",  # Basic power command
            "show system status",  # System status (might exist)
        ]

        for cmd in commands_to_try:
            try:
                _LOGGER.debug("Trying system power command: %s", cmd)
                await asyncio.sleep(0.2)  # Small delay between commands
                response = await self.socket.send_command(cmd)

                if (
                    "^" in response
                    or "unknown command" in response.lower()
                    or "label" in response
                ):
                    _LOGGER.debug("Command '%s' failed or unknown", cmd)
                    continue

                # Parse system power data using existing parser
                power_data = self._parse_system_power_response(response)

                if power_data and power_data.get("power", 0) > 0:
                    _LOGGER.info(
                        "✅ Got system power data from '%s': %.1f W",
                        cmd,
                        power_data.get("power", 0),
                    )
                    return power_data

            except Exception as err:
                _LOGGER.debug("Command '%s' failed with error: %s", cmd, err)
                continue

        _LOGGER.warning(
            "❌ All system power commands failed, will fall back to individual outlets"
        )
        return {}

    def _parse_system_power_response(self, response: str) -> Dict[str, float]:
        """Parse power data from system-wide command responses.

        Args:
            response: Raw command response

        Returns:
            Dict with parsed power values
        """
        result = {}

        if not response:
            return result

        _LOGGER.debug("Parsing system power response (%d chars)", len(response))

        # Parse total/system power - try multiple patterns
        power_patterns = [
            r"Total Power:\s*([\d.]+)\s*W",
            r"System Power:\s*([\d.]+)\s*W",
            r"PDU Power:\s*([\d.]+)\s*W",
            r"Active Power:\s*([\d.]+)\s*W",
            r"Power:\s*([\d.]+)\s*W",
        ]

        for pattern in power_patterns:
            power_match = re.search(pattern, response, re.IGNORECASE)
            if power_match:
                try:
                    result["power"] = float(power_match.group(1))
                    _LOGGER.debug(
                        "Parsed power: %.1f W (pattern: %s)", result["power"], pattern
                    )
                    break
                except (ValueError, TypeError):
                    continue

        # Parse total/system current
        current_patterns = [
            r"Total Current:\s*([\d.]+)\s*A",
            r"System Current:\s*([\d.]+)\s*A",
            r"PDU Current:\s*([\d.]+)\s*A",
            r"RMS Current:\s*([\d.]+)\s*A",
            r"Current:\s*([\d.]+)\s*A",
        ]

        for pattern in current_patterns:
            current_match = re.search(pattern, response, re.IGNORECASE)
            if current_match:
                try:
                    result["current"] = float(current_match.group(1))
                    _LOGGER.debug("Parsed current: %.3f A", result["current"])
                    break
                except (ValueError, TypeError):
                    continue

        # Parse voltage (should be same across all outlets)
        voltage_patterns = [
            r"RMS Voltage:\s*([\d.]+)\s*V",
            r"Voltage:\s*([\d.]+)\s*V",
            r"Line Voltage:\s*([\d.]+)\s*V",
        ]

        for pattern in voltage_patterns:
            voltage_match = re.search(pattern, response, re.IGNORECASE)
            if voltage_match:
                try:
                    result["voltage"] = float(voltage_match.group(1))
                    _LOGGER.debug("Parsed voltage: %.1f V", result["voltage"])
                    break
                except (ValueError, TypeError):
                    continue

        # Parse total energy
        energy_patterns = [
            r"Total Energy:\s*([\d.]+)\s*(?:kWh|Wh)",
            r"System Energy:\s*([\d.]+)\s*(?:kWh|Wh)",
            r"Active Energy:\s*([\d.]+)\s*(?:kWh|Wh)",
            r"Energy:\s*([\d.]+)\s*(?:kWh|Wh)",
        ]

        for pattern in energy_patterns:
            energy_match = re.search(pattern, response, re.IGNORECASE)
            if energy_match:
                try:
                    result["energy"] = float(energy_match.group(1))
                    _LOGGER.debug("Parsed energy: %.1f Wh", result["energy"])
                    break
                except (ValueError, TypeError):
                    continue

        # Parse frequency
        freq_patterns = [
            r"Line Frequency:\s*([\d.]+)\s*Hz",
            r"Frequency:\s*([\d.]+)\s*Hz",
        ]

        for pattern in freq_patterns:
            freq_match = re.search(pattern, response, re.IGNORECASE)
            if freq_match:
                try:
                    result["frequency"] = float(freq_match.group(1))
                    _LOGGER.debug("Parsed frequency: %.1f Hz", result["frequency"])
                    break
                except (ValueError, TypeError):
                    continue

        return result

    async def _update_outlet_metadata(self) -> None:
        """Update outlet metadata (non-critical flags) from individual outlets.

        This is less time-critical than power data, so can be done separately.
        """
        try:
            _LOGGER.debug("Updating outlet metadata (non-critical flags)")

            for outlet_num in sorted(self.outlet_states.keys()):
                if not self.outlet_states.get(outlet_num, False):
                    # Set default for powered-off outlets
                    self.outlet_non_critical[outlet_num] = False
                    continue

                try:
                    await asyncio.sleep(0.2)  # Small delay
                    response = await self.socket.send_command(
                        f"show outlets {outlet_num} details"
                    )

                    if "^" in response or "label" in response:
                        self.outlet_non_critical[outlet_num] = (
                            False  # Default to critical
                        )
                        continue

                    # Parse non-critical flag from outlet details
                    non_critical_match = re.search(
                        r"Non critical:\s*(True|False)", response
                    )
                    if non_critical_match:
                        is_non_critical = non_critical_match.group(1) == "True"
                        self.outlet_non_critical[outlet_num] = is_non_critical
                        _LOGGER.debug(
                            "Outlet %d non-critical: %s", outlet_num, is_non_critical
                        )
                    else:
                        # Default to False (critical) if not found
                        self.outlet_non_critical[outlet_num] = False

                except Exception as err:
                    _LOGGER.debug(
                        "Failed to get metadata for outlet %d: %s", outlet_num, err
                    )
                    self.outlet_non_critical[outlet_num] = False  # Safe default

        except Exception as err:
            _LOGGER.warning("Error updating outlet metadata: %s", err)

    async def _update_system_status_fallback(self) -> None:
        """Fallback method: Update system status by polling individual outlets and summing."""
        try:
            _LOGGER.debug(
                "Fetching power sensor data from all active outlets (fallback mode)"
            )

            total_power = 0.0
            total_current = 0.0
            total_energy = 0.0
            voltage_readings = []
            frequency_readings = []
            successful_readings = 0

            # Get power data from each outlet that's powered on
            for outlet_num in sorted(self.outlet_states.keys()):
                if not self.outlet_states.get(outlet_num, False):
                    _LOGGER.debug("Skipping outlet %d (powered off)", outlet_num)
                    continue

                try:
                    await asyncio.sleep(0.3)  # Prevent rapid commands
                    response = await self.socket.send_command(
                        f"show outlets {outlet_num} details"
                    )

                    if "^" in response or "label" in response:
                        _LOGGER.warning(
                            "Outlet %d details command failed - skipping", outlet_num
                        )
                        continue

                    # Parse power data from this outlet
                    power_match = re.search(r"Active Power:\s*([\d.]+)\s*W", response)
                    if power_match:
                        outlet_power = float(power_match.group(1))
                        total_power += outlet_power
                        self.outlet_power_data[outlet_num] = (
                            outlet_power  # Store individual outlet power
                        )
                        _LOGGER.debug(
                            "Outlet %d power: %.1f W", outlet_num, outlet_power
                        )

                    # Parse current from this outlet
                    current_match = re.search(r"RMS Current:\s*([\d.]+)\s*A", response)
                    if current_match:
                        outlet_current = float(current_match.group(1))
                        total_current += outlet_current

                    # Parse energy from this outlet
                    energy_match = re.search(
                        r"Active Energy:\s*([\d.]+)\s*(?:kWh|Wh)", response
                    )
                    if energy_match:
                        outlet_energy = float(energy_match.group(1))
                        total_energy += outlet_energy

                    # Collect voltage and frequency (should be same for all outlets)
                    voltage_match = re.search(r"RMS Voltage:\s*([\d.]+)\s*V", response)
                    if voltage_match:
                        voltage_readings.append(float(voltage_match.group(1)))

                    freq_match = re.search(r"Line Frequency:\s*([\d.]+)\s*Hz", response)
                    if freq_match:
                        frequency_readings.append(float(freq_match.group(1)))

                    # Parse non-critical flag from outlet details
                    non_critical_match = re.search(
                        r"Non critical:\s*(True|False)", response
                    )
                    if non_critical_match:
                        is_non_critical = non_critical_match.group(1) == "True"
                        self.outlet_non_critical[outlet_num] = is_non_critical
                        _LOGGER.debug(
                            "Outlet %d non-critical: %s", outlet_num, is_non_critical
                        )
                    else:
                        # Default to False (critical) if not found
                        self.outlet_non_critical[outlet_num] = False

                    successful_readings += 1

                except Exception as err:
                    _LOGGER.warning(
                        "Failed to get power data from outlet %d: %s", outlet_num, err
                    )
                    continue

            # Set totals
            self.active_power = total_power
            self.rms_current = total_current
            self.active_energy = total_energy

            # Use average voltage and frequency from all readings
            if voltage_readings:
                self.rms_voltage = sum(voltage_readings) / len(voltage_readings)
            else:
                self.rms_voltage = 120.0  # Default

            if frequency_readings:
                self.line_frequency = sum(frequency_readings) / len(frequency_readings)
            else:
                self.line_frequency = 60.0  # Default

            _LOGGER.info(
                "📊 Total PDU consumption (fallback): %.1f W, %.3f A, %.1f V (%d outlets measured)",
                self.active_power,
                self.rms_current,
                self.rms_voltage,
                successful_readings,
            )

        except Exception as err:
            _LOGGER.error("Error in fallback system status update: %s", err)
            raise

    async def test_direct_commands(self) -> str:
        """Test direct commands based on the syntax hints from error messages.

        This is a debug method to help determine the correct command syntax.
        """
        results = []

        # Try commands based on the syntax hint "label Outlet label (or 'all') (1/2/3/4/5/6/7/8/all)"
        commands_to_try = [
            "label 1",
            "label 2",
            "label 3",
            "label 4",
            "label 5",
            "label 6",
            "label 7",
            "label 8",
            "label all",
            "outlet 1",
            "outlet 2",
            "1",
            "2",
            "status 1",
            "status 2",
            "on 1",
            "on 2",
            "off 1",
            "off 2",
        ]

        _LOGGER.info("Beginning direct command tests...")

        for cmd in commands_to_try:
            _LOGGER.info("Testing direct command: %s", cmd)
            response = await self.socket.send_command(cmd)
            results.append(f"Command '{cmd}' response: {response}")

        return "\n".join(results)
