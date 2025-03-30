"""Middle Atlantic Racklink device implementation."""

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple, List

from .api import RacklinkApi
from .const import MODEL_CAPABILITIES, SUPPORTED_MODELS

_LOGGER = logging.getLogger(__name__)


class RacklinkDevice:
    """Representation of a Middle Atlantic Racklink device."""

    def __init__(
        self,
        host: str,
        port: int = 6000,
        username: str = None,
        password: str = None,
        model: str = None,
        scan_interval: int = 30,
        socket_timeout: float = 5.0,
        login_timeout: float = 10.0,
        command_timeout: float = 5.0,
    ):
        """Initialize the device."""
        self._api = RacklinkApi(
            host,
            port,
            username,
            password,
            socket_timeout,
            login_timeout,
            command_timeout,
        )
        self._model = model
        self._scan_interval = scan_interval

        # Device information
        self._serial_number = None
        self._firmware_version = None
        self._pdu_name = None
        self._mac_address = None

        # State tracking
        self._outlet_names = {}
        self._outlet_states = {}
        self._outlet_power_data = {}
        self._sensor_values = {}
        self._last_update = None
        self._available = False
        self._retry_count = 0
        self._max_retries = 3

        # Cached properties for access by Home Assistant
        self.outlet_power = {}
        self.outlet_current = {}
        self.outlet_energy = {}
        self.outlet_voltage = {}
        self.outlet_power_factor = {}
        self.sensors = {}
        self.pdu_info = {}

        _LOGGER.debug(
            "Initialized RackLink device for %s:%s (username: %s)",
            host,
            port,
            username,
        )

    @property
    def connected(self) -> bool:
        """Return if we are connected to the device."""
        return self._api.connected

    @property
    def available(self) -> bool:
        """Return if the device is available."""
        return self._available

    @property
    def last_error(self) -> Optional[str]:
        """Return the last error message."""
        return self._api.last_error

    @property
    def last_error_time(self) -> Optional[datetime]:
        """Return the time of the last error."""
        return self._api.last_error_time

    @property
    def pdu_model(self) -> Optional[str]:
        """Return the PDU model."""
        return self._model

    @property
    def pdu_serial(self) -> Optional[str]:
        """Return the PDU serial number."""
        return self._serial_number

    @property
    def pdu_firmware(self) -> Optional[str]:
        """Return the PDU firmware version."""
        return self._firmware_version

    @property
    def pdu_name(self) -> Optional[str]:
        """Return the PDU name."""
        return self._pdu_name

    @property
    def mac_address(self) -> Optional[str]:
        """Return the PDU MAC address."""
        return self._mac_address

    @property
    def outlet_names(self) -> Dict[int, str]:
        """Return the outlet names."""
        return self._outlet_names

    @property
    def outlet_states(self) -> Dict[int, bool]:
        """Return the outlet states."""
        return self._outlet_states

    async def start_background_connection(self) -> None:
        """Start the background connection task."""
        _LOGGER.debug("Starting background connection")
        await self._background_connect()

    async def _background_connect(self) -> bool:
        """Connect in background to avoid blocking startup."""
        _LOGGER.debug("Performing background connection")

        try:
            # Attempt to connect
            connect_success = await self._api.connect()

            if connect_success:
                _LOGGER.info("Background connection successful")
                self._available = True

                # Load initial data
                await self._load_initial_data()
                return True
            else:
                _LOGGER.warning("Background connection failed")
                self._available = False
                return False

        except Exception as e:
            _LOGGER.error("Error in background connection: %s", str(e))
            self._available = False
            return False

    async def _load_initial_data(self):
        """Load initial data from the device after connection."""
        _LOGGER.debug("Loading initial device data")
        await self.get_device_info()
        await self.get_all_outlet_states()
        await self.get_sensor_values()

    async def connect(self) -> bool:
        """Connect to the device."""
        return await self._api.connect()

    async def disconnect(self) -> bool:
        """Disconnect from the device."""
        return await self._api.disconnect()

    async def reconnect(self) -> bool:
        """Reconnect to the device."""
        _LOGGER.debug("Attempting to reconnect")

        try:
            # First, ensure disconnected
            await self._api.disconnect()

            # Small delay to allow socket to close properly
            await asyncio.sleep(0.5)

            # Attempt to connect
            connect_success = await self._api.connect()

            if connect_success:
                _LOGGER.info("Reconnection successful")
                self._available = True
                self._retry_count = 0

                # Get device info and states after reconnect
                await self.get_device_info()
                await self.get_all_outlet_states()

                return True
            else:
                _LOGGER.warning("Reconnection failed")
                self._available = False
                self._retry_count += 1
                return False

        except Exception as e:
            _LOGGER.error("Error during reconnection: %s", str(e))
            self._available = False
            self._retry_count += 1
            return False

    async def get_device_info(self) -> dict:
        """Get information about the device."""
        device_info = {}

        try:
            # If we're not connected, can't get info
            if not self._api.connected:
                _LOGGER.warning("Cannot get device info: not connected")
                return device_info

            # 1. Get model information
            if not self._model or self._model == "AUTO_DETECT":
                model_response = await self._api.send_command("show model")

                # Parse model from response
                if model_response:
                    # Extract model using regex pattern
                    model_match = re.search(
                        r"Model\s*:\s*([A-Za-z0-9\-]+)", model_response
                    )
                    if model_match:
                        self._model = model_match.group(1)
                        _LOGGER.debug("Detected model: %s", self._model)
                    else:
                        _LOGGER.warning(
                            "Could not parse model from response: %s", model_response
                        )

            # Update device_info with model
            device_info["model"] = self._model or "Unknown"

            # 2. Get serial number
            if not self._serial_number:
                serial_response = await self._api.send_command("show serial")

                # Parse serial number from response
                if serial_response:
                    # Extract serial using regex pattern
                    serial_match = re.search(
                        r"Serial\s*:\s*([A-Za-z0-9\-]+)", serial_response
                    )
                    if serial_match:
                        self._serial_number = serial_match.group(1)
                        _LOGGER.debug("Detected serial: %s", self._serial_number)
                    else:
                        _LOGGER.warning(
                            "Could not parse serial from response: %s", serial_response
                        )

            # Update device_info with serial number
            device_info["serial"] = (
                self._serial_number or f"RLNK_{self._api._host.replace('.', '_')}"
            )

            # 3. Get firmware version
            firmware_response = await self._api.send_command("show version")

            # Parse firmware version from response
            if firmware_response:
                # Extract firmware using regex pattern
                firmware_match = re.search(
                    r"Firmware Version\s*:\s*([0-9\.]+)", firmware_response
                )
                if firmware_match:
                    self._firmware_version = firmware_match.group(1)
                    _LOGGER.debug("Detected firmware: %s", self._firmware_version)
                else:
                    _LOGGER.warning(
                        "Could not parse firmware from response: %s", firmware_response
                    )

            # Update device_info with firmware version
            device_info["firmware"] = self._firmware_version or "Unknown"

            # 4. Get PDU name
            name_response = await self._api.send_command("show pdu name")

            # Parse PDU name from response
            if name_response:
                # Extract name using regex pattern
                name_match = re.search(
                    r"PDU Name\s*:\s*(.+?)$", name_response, re.MULTILINE
                )
                if name_match:
                    self._pdu_name = name_match.group(1).strip()
                    _LOGGER.debug("Detected PDU name: %s", self._pdu_name)
                else:
                    # Fallback to general name match
                    name_match = re.search(
                        r"Name\s*:\s*(.+?)$", name_response, re.MULTILINE
                    )
                    if name_match:
                        self._pdu_name = name_match.group(1).strip()
                        _LOGGER.debug(
                            "Detected PDU name (fallback): %s", self._pdu_name
                        )
                    else:
                        _LOGGER.warning(
                            "Could not parse PDU name from response: %s", name_response
                        )

            # If name is still not set, use a default
            if not self._pdu_name:
                self._pdu_name = f"RackLink PDU ({self._api._host})"

            # Update device_info with PDU name
            device_info["name"] = self._pdu_name

            # 5. Get MAC address if possible
            mac_response = await self._api.send_command("show mac")

            # Parse MAC address from response
            if mac_response:
                # Extract MAC using regex pattern
                mac_match = re.search(
                    r"MAC Address\s*:\s*([0-9A-Fa-f:]+)", mac_response
                )
                if mac_match:
                    self._mac_address = mac_match.group(1)
                    _LOGGER.debug("Detected MAC address: %s", self._mac_address)
                else:
                    _LOGGER.debug(
                        "Could not parse MAC address from response: %s", mac_response
                    )

            # Update device_info with MAC address
            device_info["mac"] = self._mac_address or "Unknown"

            # Set available state based on successful info retrieval
            self._available = True

            # Update cached pdu_info
            self.pdu_info = {
                "name": self._pdu_name,
                "model": self._model,
                "serial": self._serial_number,
                "firmware": self._firmware_version,
                "mac": self._mac_address,
            }

            return device_info

        except Exception as e:
            _LOGGER.error("Error getting device info: %s", str(e))
            self._available = False
            return device_info

    def get_model_capabilities(self) -> Dict[str, Any]:
        """Get capabilities for the current model."""
        if not self._model or self._model not in MODEL_CAPABILITIES:
            _LOGGER.debug("Using default model capabilities for model: %s", self._model)
            return MODEL_CAPABILITIES["DEFAULT"]

        _LOGGER.debug("Using specific capabilities for model: %s", self._model)
        return MODEL_CAPABILITIES[self._model]

    async def get_outlet_state(self, outlet_num: int) -> Optional[bool]:
        """Get the state of a specific outlet."""
        if not self._api.connected:
            _LOGGER.warning("Cannot get outlet state: not connected")
            return None

        try:
            # Send command to get outlet status
            response = await self._api.send_command(f"show outlet {outlet_num}")

            if not response:
                _LOGGER.warning(
                    "Empty response when getting outlet %d state", outlet_num
                )
                return None

            # Parse state from response
            if "State: ON" in response or "Status: ON" in response:
                self._outlet_states[outlet_num] = True
                return True
            elif "State: OFF" in response or "Status: OFF" in response:
                self._outlet_states[outlet_num] = False
                return False
            else:
                _LOGGER.warning(
                    "Could not parse outlet state from response: %s", response
                )
                return None

        except Exception as e:
            _LOGGER.error("Error getting outlet %d state: %s", outlet_num, str(e))
            return None

    async def get_all_outlet_states(
        self, force_refresh: bool = False
    ) -> Dict[int, bool]:
        """Get the states of all outlets."""
        if not self._api.connected:
            _LOGGER.warning("Cannot get all outlet states: not connected")
            return self._outlet_states

        # If we already have states and not forcing refresh, return cached
        if self._outlet_states and not force_refresh:
            return self._outlet_states

        try:
            # Get number of outlets from model capabilities
            capabilities = self.get_model_capabilities()
            outlet_count = capabilities.get("num_outlets", 8)

            # Send command to get all outlet statuses
            response = await self._api.send_command("show outlet all")

            if not response:
                _LOGGER.warning("Empty response when getting all outlet states")
                return self._outlet_states

            # Parse states from response
            states = {}
            names = {}

            # Parse response line by line to extract outlet information
            lines = response.splitlines()
            current_outlet = None

            for line in lines:
                # Check for outlet number
                outlet_match = re.search(r"Outlet\s+(\d+):", line)
                if outlet_match:
                    current_outlet = int(outlet_match.group(1))
                    continue

                # If we have a current outlet, look for state and name
                if current_outlet:
                    # Check for state
                    if "State: ON" in line or "Status: ON" in line:
                        states[current_outlet] = True
                    elif "State: OFF" in line or "Status: OFF" in line:
                        states[current_outlet] = False

                    # Check for name
                    name_match = re.search(r"Name:\s+(.+)$", line)
                    if name_match:
                        name = name_match.group(1).strip()
                        if name:  # Only save if not empty
                            names[current_outlet] = name

            # If we didn't find any outlets, try alternate approach
            if not states:
                _LOGGER.debug("Trying alternate parsing approach for outlet states")

                # Try to get each outlet individually
                for outlet in range(1, outlet_count + 1):
                    state = await self.get_outlet_state(outlet)
                    if state is not None:
                        states[outlet] = state

                    # Get outlet name if possible
                    if outlet not in names:
                        name_response = await self._api.send_command(
                            f"show outlet {outlet} name"
                        )
                        name_match = re.search(r"Name:\s+(.+)$", name_response)
                        if name_match:
                            name = name_match.group(1).strip()
                            if name:  # Only save if not empty
                                names[outlet] = name

            # Update internal state
            if states:
                self._outlet_states.update(states)

            # Update outlet names
            for outlet, name in names.items():
                if name:  # Only save if not empty
                    self._outlet_names[outlet] = name
                elif outlet not in self._outlet_names:
                    # Set default name if not already set
                    self._outlet_names[outlet] = f"Outlet {outlet}"

            # Ensure all outlets have names
            for outlet in range(1, outlet_count + 1):
                if outlet not in self._outlet_names:
                    self._outlet_names[outlet] = f"Outlet {outlet}"

            # Update available state
            self._available = True

            return self._outlet_states

        except Exception as e:
            _LOGGER.error("Error getting all outlet states: %s", str(e))
            self._available = False
            return self._outlet_states

    async def set_outlet_state(self, outlet_num: int, state: bool) -> bool:
        """Set the state of a specific outlet."""
        if not self._api.connected:
            _LOGGER.warning("Cannot set outlet state: not connected")
            return False

        try:
            # Send command to set outlet state
            command = f"set outlet {outlet_num} {'on' if state else 'off'}"
            response = await self._api.send_command(command)

            # Check if command was successful
            if "Error" in response or "Failed" in response:
                _LOGGER.error("Failed to set outlet %d state: %s", outlet_num, response)
                return False

            # Update internal state
            self._outlet_states[outlet_num] = state
            _LOGGER.debug("Set outlet %d to %s", outlet_num, "ON" if state else "OFF")

            return True

        except Exception as e:
            _LOGGER.error("Error setting outlet %d state: %s", outlet_num, str(e))
            return False

    async def cycle_outlet(self, outlet_num: int) -> bool:
        """Cycle power on a specific outlet."""
        if not self._api.connected:
            _LOGGER.warning("Cannot cycle outlet: not connected")
            return False

        try:
            # Send command to cycle outlet
            command = f"cycle outlet {outlet_num}"
            response = await self._api.send_command(command)

            # Check if command was successful
            if "Error" in response or "Failed" in response:
                _LOGGER.error("Failed to cycle outlet %d: %s", outlet_num, response)
                return False

            _LOGGER.debug("Cycled outlet %d", outlet_num)

            # Wait a moment for the outlet to start cycling
            await asyncio.sleep(1)

            # Update the outlet state to reflect the change
            await self.get_outlet_state(outlet_num)

            return True

        except Exception as e:
            _LOGGER.error("Error cycling outlet %d: %s", outlet_num, str(e))
            return False

    async def cycle_all_outlets(self) -> bool:
        """Cycle power on all outlets."""
        if not self._api.connected:
            _LOGGER.warning("Cannot cycle all outlets: not connected")
            return False

        try:
            # Send command to cycle all outlets
            command = "cycle outlet all"
            response = await self._api.send_command(command)

            # Check if command was successful
            if "Error" in response or "Failed" in response:
                _LOGGER.error("Failed to cycle all outlets: %s", response)
                return False

            _LOGGER.debug("Cycled all outlets")

            # Wait a moment for the outlets to start cycling
            await asyncio.sleep(1)

            # Update all outlet states to reflect the change
            await self.get_all_outlet_states(force_refresh=True)

            return True

        except Exception as e:
            _LOGGER.error("Error cycling all outlets: %s", str(e))
            return False

    async def set_outlet_name(self, outlet_num: int, name: str) -> bool:
        """Set the name of a specific outlet."""
        if not self._api.connected:
            _LOGGER.warning("Cannot set outlet name: not connected")
            return False

        try:
            # Send command to set outlet name
            command = f"set outlet {outlet_num} name {name}"
            response = await self._api.send_command(command)

            # Check if command was successful
            if "Error" in response or "Failed" in response:
                _LOGGER.error("Failed to set outlet %d name: %s", outlet_num, response)
                return False

            # Update internal state
            self._outlet_names[outlet_num] = name
            _LOGGER.debug("Set outlet %d name to %s", outlet_num, name)

            return True

        except Exception as e:
            _LOGGER.error("Error setting outlet %d name: %s", outlet_num, str(e))
            return False

    async def set_pdu_name(self, name: str) -> bool:
        """Set the name of the PDU."""
        if not self._api.connected:
            _LOGGER.warning("Cannot set PDU name: not connected")
            return False

        try:
            # Send command to set PDU name
            command = f"set pdu name {name}"
            response = await self._api.send_command(command)

            # Check if command was successful
            if "Error" in response or "Failed" in response:
                _LOGGER.error("Failed to set PDU name: %s", response)
                return False

            # Update internal state
            self._pdu_name = name
            _LOGGER.debug("Set PDU name to %s", name)

            # Update device info
            await self.get_device_info()

            return True

        except Exception as e:
            _LOGGER.error("Error setting PDU name: %s", str(e))
            return False

    async def get_sensor_values(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Get sensor values from the device."""
        if not self._api.connected:
            _LOGGER.warning("Cannot get sensor values: not connected")
            return self._sensor_values

        # If we already have values and not forcing refresh, return cached
        if self._sensor_values and not force_refresh:
            return self._sensor_values

        try:
            # Get capabilities to determine if we should look for sensor data
            capabilities = self.get_model_capabilities()
            has_current_sensing = capabilities.get("has_current_sensing", False)

            # If the model doesn't support sensors, return empty dict
            if not has_current_sensing:
                _LOGGER.debug("Model %s does not support sensors", self._model)
                self._sensor_values = {}
                self.sensors = {}
                return self._sensor_values

            # Send command to get sensor data
            response = await self._api.send_command("show sensors")

            if not response:
                _LOGGER.warning("Empty response when getting sensor values")
                return self._sensor_values

            # Initialize sensor values
            sensor_values = {}

            # Parse temperature
            temp_match = re.search(r"Temperature:\s*([\d\.]+)\s*(?:C|F)", response)
            if temp_match:
                sensor_values["temperature"] = float(temp_match.group(1))

            # Parse current
            current_match = re.search(r"Current:\s*([\d\.]+)\s*A", response)
            if current_match:
                sensor_values["current"] = float(current_match.group(1))

            # Parse voltage
            voltage_match = re.search(r"Voltage:\s*([\d\.]+)\s*V", response)
            if voltage_match:
                sensor_values["voltage"] = float(voltage_match.group(1))

            # Parse power
            power_match = re.search(r"Power:\s*([\d\.]+)\s*W", response)
            if power_match:
                sensor_values["power"] = float(power_match.group(1))

            # Parse energy (kWh often shown as Energy or Power Consumption)
            energy_match = re.search(
                r"(?:Energy|Power Consumption):\s*([\d\.]+)\s*(?:kWh|kW/h)", response
            )
            if energy_match:
                sensor_values["energy"] = float(energy_match.group(1))

            # Parse power factor
            pf_match = re.search(r"Power Factor:\s*([\d\.]+)", response)
            if pf_match:
                sensor_values["power_factor"] = float(pf_match.group(1))

            # Parse frequency
            freq_match = re.search(r"Frequency:\s*([\d\.]+)\s*Hz", response)
            if freq_match:
                sensor_values["frequency"] = float(freq_match.group(1))

            # Update internal state
            self._sensor_values = sensor_values
            self.sensors = sensor_values.copy()

            # Update available state
            self._available = True

            return self._sensor_values

        except Exception as e:
            _LOGGER.error("Error getting sensor values: %s", str(e))
            return self._sensor_values

    async def get_outlet_power_data(self, outlet_num: int) -> Dict[str, Any]:
        """Get power data for a specific outlet."""
        if not self._api.connected:
            _LOGGER.warning("Cannot get outlet power data: not connected")
            return {}

        # Check if model supports current sensing
        capabilities = self.get_model_capabilities()
        has_current_sensing = capabilities.get("has_current_sensing", False)

        if not has_current_sensing:
            _LOGGER.debug("Model %s does not support outlet power data", self._model)
            return {}

        try:
            # Send command to get outlet power data
            response = await self._api.send_command(f"show outlet {outlet_num} power")

            if not response:
                _LOGGER.warning(
                    "Empty response when getting outlet %d power data", outlet_num
                )
                return {}

            # Parse power data
            power_data = {}

            # Parse current
            current_match = re.search(r"Current:\s*([\d\.]+)\s*A", response)
            if current_match:
                power_data["current"] = float(current_match.group(1))
                self.outlet_current[outlet_num] = float(current_match.group(1))

            # Parse voltage
            voltage_match = re.search(r"Voltage:\s*([\d\.]+)\s*V", response)
            if voltage_match:
                power_data["voltage"] = float(voltage_match.group(1))
                self.outlet_voltage[outlet_num] = float(voltage_match.group(1))

            # Parse power
            power_match = re.search(r"Power:\s*([\d\.]+)\s*W", response)
            if power_match:
                power_data["power"] = float(power_match.group(1))
                self.outlet_power[outlet_num] = float(power_match.group(1))

            # Parse energy
            energy_match = re.search(
                r"(?:Energy|Power Consumption):\s*([\d\.]+)\s*(?:kWh|kW/h)", response
            )
            if energy_match:
                power_data["energy"] = float(energy_match.group(1))
                self.outlet_energy[outlet_num] = float(energy_match.group(1))

            # Parse power factor
            pf_match = re.search(r"Power Factor:\s*([\d\.]+)", response)
            if pf_match:
                power_data["power_factor"] = float(pf_match.group(1))
                self.outlet_power_factor[outlet_num] = float(pf_match.group(1))

            # Update internal state
            if outlet_num not in self._outlet_power_data:
                self._outlet_power_data[outlet_num] = {}

            self._outlet_power_data[outlet_num] = power_data

            return power_data

        except Exception as e:
            _LOGGER.error("Error getting outlet %d power data: %s", outlet_num, str(e))
            return {}

    async def get_all_power_data(self, sample_size: int = 3) -> bool:
        """Get power data for all outlets."""
        if not self._api.connected:
            _LOGGER.warning("Cannot get all power data: not connected")
            return False

        # Check if model supports current sensing
        capabilities = self.get_model_capabilities()
        has_current_sensing = capabilities.get("has_current_sensing", False)
        outlet_count = capabilities.get("num_outlets", 8)

        if not has_current_sensing:
            _LOGGER.debug("Model %s does not support power data", self._model)
            return False

        try:
            # Collect power data for all outlets
            for outlet in range(1, outlet_count + 1):
                await self.get_outlet_power_data(outlet)

            return True

        except Exception as e:
            _LOGGER.error("Error getting all power data: %s", str(e))
            return False

    async def update(self) -> bool:
        """Update device data."""
        if not self._api.connected:
            _LOGGER.warning("Cannot update device data: not connected")
            return False

        try:
            # Get outlet states
            await self.get_all_outlet_states()

            # Get sensor values
            await self.get_sensor_values()

            # Get power data if the model supports it
            capabilities = self.get_model_capabilities()
            has_current_sensing = capabilities.get("has_current_sensing", False)

            if has_current_sensing:
                await self.get_all_power_data()

            # Update timestamp
            self._last_update = time.time()

            # Update available state
            self._available = True

            return True

        except Exception as e:
            _LOGGER.error("Error updating device data: %s", str(e))
            self._available = False
            return False

    async def shutdown(self) -> None:
        """Shutdown the device connection."""
        _LOGGER.debug("Shutting down connection")
        await self._api.disconnect()
