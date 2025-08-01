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

        # Initialize load shedding defaults immediately for reliable binary sensors
        self._initialize_load_shedding_defaults()
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
        """Update system status with conservative approach to prevent session corruption."""
        try:
            # Use conservative approach - just get power data from outlet 1 as representative
            # This prevents session corruption from too many rapid commands
            _LOGGER.debug(
                "Getting representative power data from outlet 1 (conservative mode)"
            )

            await asyncio.sleep(1.0)  # Longer delay to prevent session corruption
            response = await self.socket.send_command("show outlets 1 details")

            if "^" in response or "label" in response:
                _LOGGER.warning("Outlet 1 details command failed - using defaults")
                # Use safe defaults
                self.rms_voltage = 120.0
                self.rms_current = 0.0
                self.active_power = 0.0
                self.active_energy = 0.0
                self.line_frequency = 60.0
            else:
                # Parse power data from outlet 1 as representative of the system
                self._parse_outlet_power_data(response)

                # Estimate total system power based on number of active outlets
                active_outlets = sum(
                    1 for state in self.outlet_states.values() if state
                )
                if active_outlets > 1 and self.active_power > 0:
                    # Simple estimation: multiply by number of active outlets
                    estimated_total = self.active_power * active_outlets
                    _LOGGER.debug(
                        "Estimating total power: %.1f W (outlet 1: %.1f W × %d active outlets)",
                        estimated_total,
                        self.active_power,
                        active_outlets,
                    )
                    self.active_power = estimated_total
                    self.rms_current = self.rms_current * active_outlets

                _LOGGER.info(
                    "📊 PDU Status (conservative): %.1f W, %.3f A, %.1f V (%d active outlets)",
                    self.active_power,
                    self.rms_current,
                    self.rms_voltage,
                    active_outlets,
                )

            # Update outlet criticality flags for load shedding without excessive commands
            await self.update_outlet_non_critical_flags()

            # Set default values for advanced features
            self.load_shedding_active = False
            self.sequence_active = False

        except Exception as err:
            _LOGGER.error("Error updating system status: %s", err)
            raise

    def _parse_outlet_power_data(self, response: str) -> None:
        """Parse power data from a single outlet response."""
        # Parse voltage
        voltage_match = re.search(r"RMS Voltage:\s*([\d.]+)\s*V", response)
        if voltage_match:
            self.rms_voltage = float(voltage_match.group(1))
        else:
            self.rms_voltage = 120.0  # Default

        # Parse current
        current_match = re.search(r"RMS Current:\s*([\d.]+)\s*A", response)
        if current_match:
            self.rms_current = float(current_match.group(1))
        else:
            self.rms_current = 0.0

        # Parse power
        power_match = re.search(r"Active Power:\s*([\d.]+)\s*W", response)
        if power_match:
            self.active_power = float(power_match.group(1))
        else:
            self.active_power = self.rms_voltage * self.rms_current  # Calculate

        # Parse energy
        energy_match = re.search(r"Active Energy:\s*([\d.]+)\s*(?:kWh|Wh)", response)
        if energy_match:
            self.active_energy = float(energy_match.group(1))
        else:
            self.active_energy = 0.0

        # Parse frequency
        freq_match = re.search(r"Line Frequency:\s*([\d.]+)\s*Hz", response)
        if freq_match:
            self.line_frequency = float(freq_match.group(1))
        else:
            self.line_frequency = 60.0  # Default

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
        """Cycle all outlets using Telnet commands."""
        try:
            _LOGGER.info("🔄 Cycling all outlets (%d outlets)", len(self.outlet_states))

            success_count = 0
            total_outlets = len(self.outlet_states)

            # Try individual outlet cycle commands via Telnet
            for outlet_num in sorted(self.outlet_states.keys()):
                cmd = f"power outlets {outlet_num} cycle /y"
                _LOGGER.debug("📤 Sending cycle command: %s", cmd)
                response = await self.socket.send_command(cmd)

                # Check for success (empty response or no error indicators)
                success = (
                    len(response.strip()) == 0
                    or "unknown command" not in response.lower()
                    and "invalid" not in response.lower()
                    and "error" not in response.lower()
                )

                if success:
                    success_count += 1
                    _LOGGER.debug("✅ Successfully cycled outlet %d", outlet_num)
                else:
                    _LOGGER.warning(
                        "❌ Failed to cycle outlet %d. Response: %r",
                        outlet_num,
                        response,
                    )

                # Small delay between commands to prevent session issues
                await asyncio.sleep(0.5)

            if success_count > 0:
                _LOGGER.info(
                    "✅ Successfully cycled %d of %d outlets",
                    success_count,
                    total_outlets,
                )
                # Wait for the cycles to complete
                _LOGGER.debug("⏱️ Waiting for outlet cycles to complete")
                await asyncio.sleep(cycle_time + 2)
                # Update all successfully cycled outlets to on
                for outlet_num in self.outlet_states:
                    self.outlet_states[outlet_num] = True
                _LOGGER.debug("Updated all outlet states to ON after cycle")
                return success_count == total_outlets
            else:
                _LOGGER.warning("❌ Failed to cycle any outlets")
                return False

        except Exception as err:
            _LOGGER.error("Error cycling all outlets: %s", err)
            return False

    async def start_load_shedding(self) -> bool:
        """Start load shedding using proper CLI configuration command."""
        try:
            _LOGGER.info("🔄 Configuring load shedding (outlets 6+ as non-critical)")

            # Use proper CLI configuration command from official documentation
            # Mark outlets 1-5 as critical (false) and 6-8+ as non-critical (true)
            critical_outlets = []
            non_critical_outlets = []

            for outlet_num in self.outlet_states.keys():
                if outlet_num >= 6:
                    non_critical_outlets.append(str(outlet_num))
                else:
                    critical_outlets.append(str(outlet_num))

            # Build the configuration command
            critical_list = ",".join(critical_outlets) if critical_outlets else ""
            non_critical_list = (
                ",".join(non_critical_outlets) if non_critical_outlets else ""
            )

            cmd_parts = []
            if critical_list:
                cmd_parts.append(f"{critical_list}:false")
            if non_critical_list:
                cmd_parts.append(f"{non_critical_list}:true")

            cmd = f"config:# pdu nonCriticalOutlets {';'.join(cmd_parts)}"

            _LOGGER.info("📤 Sending load shedding config: %s", cmd)
            response = await self.socket.send_command(cmd)
            _LOGGER.debug("Load shedding config response: %r", response)

            # Check for success indicators or absence of error messages
            success = (
                len(response.strip()) == 0  # Empty response often means success
                or "config" in response.lower()
                or "unknown command" not in response.lower()
                and "invalid" not in response.lower()
                and "error" not in response.lower()
            )

            if success:
                _LOGGER.info("✅ Successfully configured load shedding")
                self.load_shedding_active = True
                # Update our internal state
                for outlet_num in self.outlet_states.keys():
                    self.outlet_non_critical[outlet_num] = outlet_num >= 6
                return True
            else:
                _LOGGER.warning(
                    "❌ Failed to configure load shedding. Response: %r", response
                )
                return False

        except Exception as err:
            _LOGGER.error("Error configuring load shedding: %s", err)
            return False

    async def stop_load_shedding(self) -> bool:
        """Stop load shedding by marking all outlets as critical."""
        try:
            _LOGGER.info("🔄 Disabling load shedding (all outlets as critical)")

            # Use proper CLI configuration command to mark all outlets as critical (false)
            all_outlets = ",".join(
                str(outlet_num) for outlet_num in self.outlet_states.keys()
            )
            cmd = f"config:# pdu nonCriticalOutlets {all_outlets}:false"

            _LOGGER.info("📤 Sending load shedding disable config: %s", cmd)
            response = await self.socket.send_command(cmd)
            _LOGGER.debug("Load shedding disable response: %r", response)

            # Check for success indicators or absence of error messages
            success = (
                len(response.strip()) == 0  # Empty response often means success
                or "config" in response.lower()
                or "unknown command" not in response.lower()
                and "invalid" not in response.lower()
                and "error" not in response.lower()
            )

            if success:
                _LOGGER.info("✅ Successfully disabled load shedding")
                self.load_shedding_active = False
                # Update our internal state - all outlets are now critical
                for outlet_num in self.outlet_states.keys():
                    self.outlet_non_critical[outlet_num] = False
                return True
            else:
                _LOGGER.warning(
                    "❌ Failed to disable load shedding. Response: %r", response
                )
                return False

        except Exception as err:
            _LOGGER.error("Error disabling load shedding: %s", err)
            return False

    async def start_sequence(self) -> bool:
        """Start outlet sequencing using proper CLI configuration."""
        try:
            _LOGGER.info("🔄 Configuring outlet startup sequence")

            # Configure a default startup sequence (1, 2, 3, 4, 5, 6, 7, 8)
            # This will make outlets power on in order when the PDU starts
            outlet_list = sorted(self.outlet_states.keys())
            sequence_order = ",".join(str(outlet) for outlet in outlet_list)

            # Set the outlet sequence using proper CLI command
            sequence_cmd = f"config:# pdu outletSequence {sequence_order}"
            _LOGGER.info("📤 Sending sequence config: %s", sequence_cmd)
            response = await self.socket.send_command(sequence_cmd)
            _LOGGER.debug("Sequence config response: %r", response)

            # Also set a 2-second delay between each outlet
            delay_cmd = f"config:# pdu outletSequenceDelay {';'.join(f'{outlet}:2' for outlet in outlet_list)}"
            _LOGGER.info("📤 Sending sequence delay config: %s", delay_cmd)
            delay_response = await self.socket.send_command(delay_cmd)
            _LOGGER.debug("Sequence delay response: %r", delay_response)

            # Check for success (empty response or no error indicators)
            success = (
                len(response.strip()) == 0
                or "unknown command" not in response.lower()
                and "invalid" not in response.lower()
                and "error" not in response.lower()
            ) and (
                len(delay_response.strip()) == 0
                or "unknown command" not in delay_response.lower()
                and "invalid" not in delay_response.lower()
                and "error" not in delay_response.lower()
            )

            if success:
                _LOGGER.info("✅ Successfully configured outlet sequence")
                self.sequence_active = True
                return True
            else:
                _LOGGER.warning(
                    "❌ Failed to configure sequence. Response: %r, Delay: %r",
                    response,
                    delay_response,
                )
                return False

        except Exception as err:
            _LOGGER.error("Error configuring sequence: %s", err)
            return False

    async def stop_sequence(self) -> bool:
        """Disable outlet sequencing by setting to default behavior."""
        try:
            _LOGGER.info("🔄 Disabling outlet sequence (setting to default)")

            # Set sequence to "default" which should disable custom sequencing
            sequence_cmd = "config:# pdu outletSequence default"
            _LOGGER.info("📤 Sending sequence disable config: %s", sequence_cmd)
            response = await self.socket.send_command(sequence_cmd)
            _LOGGER.debug("Sequence disable response: %r", response)

            # Remove custom delays by setting to 0 or default
            outlet_list = sorted(self.outlet_states.keys())
            delay_cmd = f"config:# pdu outletSequenceDelay {';'.join(f'{outlet}:0' for outlet in outlet_list)}"
            _LOGGER.info("📤 Sending sequence delay reset: %s", delay_cmd)
            delay_response = await self.socket.send_command(delay_cmd)
            _LOGGER.debug("Sequence delay reset response: %r", delay_response)

            # Check for success (empty response or no error indicators)
            success = (
                len(response.strip()) == 0
                or "unknown command" not in response.lower()
                and "invalid" not in response.lower()
                and "error" not in response.lower()
            ) and (
                len(delay_response.strip()) == 0
                or "unknown command" not in delay_response.lower()
                and "invalid" not in delay_response.lower()
                and "error" not in delay_response.lower()
            )

            if success:
                _LOGGER.info("✅ Successfully disabled outlet sequence")
                self.sequence_active = False
                return True
            else:
                _LOGGER.warning(
                    "❌ Failed to disable sequence. Response: %r, Delay: %r",
                    response,
                    delay_response,
                )
                return False

        except Exception as err:
            _LOGGER.error("Error disabling sequence: %s", err)
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

    def _initialize_load_shedding_defaults(self) -> None:
        """Initialize load shedding defaults for reliable binary sensor startup."""
        # Set defaults for common outlet configurations (1-8 outlets)
        # This ensures binary sensors work immediately without waiting for device connection
        for outlet_num in range(1, 9):  # Outlets 1-8
            if outlet_num >= 6:  # Outlets 6-8 typically non-critical
                self.outlet_non_critical[outlet_num] = True
            else:
                self.outlet_non_critical[outlet_num] = False  # Critical outlets 1-5

        _LOGGER.debug(
            "Initialized load shedding defaults: outlets 1-5 critical, 6-8 non-critical"
        )

    async def update_outlet_non_critical_flags(self) -> None:
        """Update non-critical flags for outlets in a simple, stable way."""
        try:
            # Update flags only for actually discovered outlets
            # This maintains the defaults but updates based on real device state
            if self.outlet_states:
                for outlet_num in self.outlet_states.keys():
                    if outlet_num >= 6:  # Outlets 6+ are often non-critical
                        self.outlet_non_critical[outlet_num] = True
                    else:
                        self.outlet_non_critical[outlet_num] = (
                            False  # Critical outlets 1-5
                        )

                _LOGGER.debug(
                    "Updated outlet criticality for discovered outlets: %s",
                    list(self.outlet_states.keys()),
                )
            else:
                # Keep defaults if no outlets discovered yet
                _LOGGER.debug(
                    "No outlets discovered yet, keeping load shedding defaults"
                )

        except Exception as err:
            _LOGGER.warning("Error setting outlet criticality: %s", err)
            # Maintain safe defaults - don't clear existing values
            _LOGGER.debug(
                "Maintained existing load shedding configuration due to error"
            )

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
