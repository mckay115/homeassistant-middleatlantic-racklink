"""Base controller module for Middle Atlantic RackLink integration."""

import asyncio
import logging
import re
from asyncio.exceptions import CancelledError
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from ..const import (
    COMMAND_DISCOVERY_ATTEMPTS,
    COMMAND_QUERY_DELAY,
    DEFAULT_TIMEOUT,
    DEVICE_TYPES,
    MAX_CONNECTION_ATTEMPTS,
    MAX_FAILED_COMMANDS,
    SENSOR_PDU_CURRENT,
    SENSOR_PDU_ENERGY,
    SENSOR_PDU_FREQUENCY,
    SENSOR_PDU_POWER,
    SENSOR_PDU_POWER_FACTOR,
    SENSOR_PDU_TEMPERATURE,
    SENSOR_PDU_VOLTAGE,
)
from ..parser import (
    parse_available_commands,
    parse_device_info,
    parse_all_outlet_states,
    parse_pdu_power_data,
    parse_pdu_temperature,
)
from ..socket_connection import SocketConnection

_LOGGER = logging.getLogger(__name__)


class BaseController:
    """Base controller for Middle Atlantic RackLink PDU."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        pdu_name: str = None,
        connection_timeout: int = DEFAULT_TIMEOUT,
        command_timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize the controller."""
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self._pdu_name = pdu_name or "RackLink PDU"
        self.connection_timeout = connection_timeout
        self.command_timeout = command_timeout

        # Connection and status tracking
        self._connection = None
        self._connected = False
        self._available = False
        self._stopping = False
        self._error_count = 0
        self._connect_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()
        self._command_queue = asyncio.Queue()
        self._command_processor_task = None

        # PDU information
        self.pdu_model = None
        self.pdu_serial = None
        self.pdu_firmware = None
        self.mac_address = None
        self._model_capabilities = None
        self._available_commands = set()
        self._command_discovery_complete = False

        # PDU data
        self.outlet_states = {}
        self.outlet_names = {}
        self.sensors = {}
        self.outlet_current = {}
        self.outlet_power = {}
        self.outlet_energy = {}
        self.outlet_voltage = {}
        self.outlet_power_factor = {}
        self.outlet_apparent_power = {}
        self.outlet_line_frequency = {}

    @property
    def pdu_name(self) -> str:
        """Return the PDU name."""
        return self._pdu_name

    @pdu_name.setter
    def pdu_name(self, value: str) -> None:
        """Set the PDU name."""
        self._pdu_name = value

    @property
    def connected(self) -> bool:
        """Return True if connected to the PDU."""
        return self._connected

    @property
    def available(self) -> bool:
        """Return True if the PDU is available."""
        return self._available

    async def connect(self) -> bool:
        """Connect to the RackLink PDU."""
        async with self._connect_lock:
            if self._connected:
                return True

            _LOGGER.debug("Connecting to %s:%d", self.host, self.port)

            try:
                connection = SocketConnection(
                    self.host,
                    self.port,
                    self.username,
                    self.password,
                    timeout=self.connection_timeout,
                )

                await connection.connect()
                self._connection = connection
                self._connected = True
                self._available = True
                self._error_count = 0

                # Start command processor if not already running
                if (
                    not self._command_processor_task
                    or self._command_processor_task.done()
                ):
                    self._command_processor_task = asyncio.create_task(
                        self._process_command_queue()
                    )

                # Get device details
                await self._get_device_info()

                # Discover available commands
                if not self._command_discovery_complete:
                    await self._discover_available_commands()

                _LOGGER.info(
                    "Connected to %s (%s) at %s",
                    self.pdu_name,
                    self.pdu_model or "Unknown",
                    self.host,
                )

                return True

            except Exception as err:
                self._connected = False
                self._available = False
                _LOGGER.error(
                    "Failed to connect to %s:%d - %s", self.host, self.port, err
                )
                return False

    async def disconnect(self) -> None:
        """Disconnect from the RackLink PDU."""
        self._stopping = True

        # Cancel the command processor task
        if self._command_processor_task and not self._command_processor_task.done():
            self._command_processor_task.cancel()
            try:
                await self._command_processor_task
            except CancelledError:
                pass

        # Close the connection
        if self._connection:
            await self._connection.disconnect()
            self._connection = None

        self._connected = False
        self._available = False
        _LOGGER.debug("Disconnected from %s", self.host)

    async def _process_command_queue(self) -> None:
        """Process commands from the queue to prevent overwhelming the device."""
        try:
            while not self._stopping:
                # Get the next command from the queue
                command, future = await self._command_queue.get()

                try:
                    # Execute the command
                    result = await self._send_command(command)

                    # Set the result
                    if not future.done():
                        future.set_result(result)

                except Exception as err:
                    # Set the exception
                    if not future.done():
                        future.set_exception(err)

                # Mark the task as done
                self._command_queue.task_done()

                # Wait a short delay to prevent overwhelming the device
                if not self._stopping:
                    await asyncio.sleep(COMMAND_QUERY_DELAY)

        except CancelledError:
            # Handle normal cancellation
            _LOGGER.debug("Command processor task cancelled")

        except Exception as err:
            # Handle unexpected errors
            _LOGGER.error("Error in command processor: %s", err)

        finally:
            # Make sure all remaining commands are marked with an exception
            while not self._command_queue.empty():
                try:
                    _, future = self._command_queue.get_nowait()
                    if not future.done():
                        future.set_exception(
                            ConnectionError("Command processor stopped")
                        )
                    self._command_queue.task_done()
                except asyncio.QueueEmpty:
                    break

    async def queue_command(self, command: str) -> str:
        """Queue a command for execution by the command processor."""
        if not self._connected:
            _LOGGER.warning("Cannot queue command, not connected")
            raise ConnectionError("Not connected to device")

        # Create a future for the result
        future = asyncio.Future()

        # Add the command to the queue
        await self._command_queue.put((command, future))

        try:
            # Wait for the result with timeout
            return await asyncio.wait_for(future, timeout=self.command_timeout)
        except asyncio.TimeoutError:
            _LOGGER.error("Command timed out: %s", command)
            raise TimeoutError(f"Command timed out: {command}")
        except Exception as err:
            _LOGGER.error("Error executing command %s: %s", command, err)
            raise

    async def _send_command(self, command: str) -> str:
        """Send a command to the PDU and return the response."""
        if not self._connected or not self._connection:
            _LOGGER.warning("Cannot send command, not connected")
            self._available = False
            raise ConnectionError("Not connected to device")

        async with self._command_lock:
            try:
                response = await self._connection.send_command(command)

                # Check for errors in the response
                if "error" in response.lower() or "unknown command" in response.lower():
                    _LOGGER.warning(
                        "Command error: %s - Response: %s", command, response
                    )
                    self._error_count += 1

                    if self._error_count >= MAX_FAILED_COMMANDS:
                        _LOGGER.error("Too many command errors, marking as unavailable")
                        self._available = False
                        raise ConnectionError("Too many command errors")
                else:
                    # Reset error count on successful command
                    self._error_count = 0
                    self._available = True

                return response

            except Exception as err:
                _LOGGER.error("Error sending command %s: %s", command, err)
                self._error_count += 1

                if self._error_count >= MAX_FAILED_COMMANDS:
                    _LOGGER.error("Too many command errors, marking as unavailable")
                    self._available = False

                raise

    async def _get_device_info(self) -> bool:
        """Get device information from the PDU."""
        try:
            # Try different commands for getting device info
            commands = ["show device", "show system", "show pdu"]

            for cmd in commands:
                try:
                    if not self._connected:
                        return False

                    response = await self._send_command(cmd)
                    device_info = parse_device_info(response)

                    if device_info:
                        self.pdu_model = device_info.get("model")
                        self.pdu_serial = device_info.get("serial")
                        self.pdu_firmware = device_info.get("firmware")
                        self.mac_address = device_info.get("mac")

                        _LOGGER.info(
                            "Device info: Model=%s, Serial=%s, Firmware=%s, MAC=%s",
                            self.pdu_model,
                            self.pdu_serial,
                            self.pdu_firmware,
                            self.mac_address,
                        )
                        return True

                except Exception as err:
                    _LOGGER.debug("Error getting device info with %s: %s", cmd, err)
                    continue

            _LOGGER.warning("Could not get device information with any command")
            return False

        except Exception as err:
            _LOGGER.error("Error getting device info: %s", err)
            return False

    async def _discover_available_commands(self) -> None:
        """Discover available commands from the PDU."""
        commands_discovered = False

        for attempt in range(COMMAND_DISCOVERY_ATTEMPTS):
            try:
                _LOGGER.debug(
                    "Discovering available commands (attempt %d)", attempt + 1
                )

                # Try different help commands
                help_commands = ["help", "?", "help all", "menu"]

                for help_cmd in help_commands:
                    try:
                        response = await self._send_command(help_cmd)
                        commands = parse_available_commands(response)

                        if commands:
                            _LOGGER.info("Discovered %d commands", len(commands))
                            self._available_commands = set(commands)
                            self._command_discovery_complete = True
                            commands_discovered = True
                            break

                    except Exception as err:
                        _LOGGER.debug(
                            "Error discovering commands with %s: %s", help_cmd, err
                        )
                        continue

                if commands_discovered:
                    break

            except Exception as err:
                _LOGGER.error(
                    "Error discovering commands (attempt %d): %s", attempt + 1, err
                )

            # Wait before retrying
            await asyncio.sleep(1)

        if not commands_discovered:
            _LOGGER.warning("Could not discover available commands, will use defaults")
            # Set a default set of common commands
            self._available_commands = {
                "show",
                "set",
                "cycle",
                "on",
                "off",
                "reboot",
                "config",
                "apply",
                "outlet",
                "pdu",
                "power",
            }

        _LOGGER.debug("Available commands: %s", sorted(list(self._available_commands)))

    def get_model_capabilities(self) -> Dict[str, Any]:
        """Get capabilities for the current PDU model."""
        if self._model_capabilities is not None:
            return self._model_capabilities

        capabilities = {
            "num_outlets": 8,  # Default to 8 outlets
            "has_current_sensing": True,
            "has_outlet_energy": False,
            "has_outlet_names": True,
            "has_outlet_groups": False,
        }

        # Determine capabilities based on model if available
        if self.pdu_model:
            model_lower = self.pdu_model.lower()

            # Check for specific models
            if "rlnk-415-1" in model_lower:
                capabilities["num_outlets"] = 4
            elif "rlnk-915" in model_lower or "rlnk-920" in model_lower:
                capabilities["num_outlets"] = 9
            elif "rlnk-1115" in model_lower or "rlnk-1120" in model_lower:
                capabilities["num_outlets"] = 11
            elif "rlnk-215" in model_lower or "rlnk-220" in model_lower:
                capabilities["num_outlets"] = 2

            # Check for advanced models with energy monitoring
            if (
                "20" in model_lower
            ):  # Typically models ending in 20 have advanced monitoring
                capabilities["has_outlet_energy"] = True

            # Look for outlet group support
            if "sw" in model_lower or any(
                x in model_lower for x in ["1115", "1120", "915", "920"]
            ):
                capabilities["has_outlet_groups"] = True

        self._model_capabilities = capabilities
        return capabilities

    async def update(self) -> bool:
        """Update PDU data from device."""
        if not self._connected:
            try:
                if not await self.connect():
                    return False
            except Exception as err:
                _LOGGER.error("Failed to connect during update: %s", err)
                return False

        try:
            # Update outlet states
            await self._update_outlet_states()

            # Update power data
            await self._update_power_data()

            # Update temperature data
            await self._update_temperature()

            # Update successful
            return True

        except Exception as err:
            _LOGGER.error("Error updating PDU data: %s", err)
            self._available = False
            return False

    async def _update_outlet_states(self) -> None:
        """Update outlet states from the PDU."""
        try:
            # First try 'show outlets'
            response = await self.queue_command("show outlets")
            outlet_data = parse_all_outlet_states(response)

            if not outlet_data:
                # Try alternative command 'show outlet'
                response = await self.queue_command("show outlet")
                outlet_data = parse_all_outlet_states(response)

            if outlet_data:
                self.outlet_states = outlet_data.get("states", {})
                self.outlet_names = outlet_data.get("names", {})

                _LOGGER.debug(
                    "Updated outlet states: %s",
                    {f"Outlet {k}": v for k, v in self.outlet_states.items()},
                )

        except Exception as err:
            _LOGGER.error("Error updating outlet states: %s", err)
            raise

    async def _update_power_data(self) -> None:
        """Update power data from the PDU."""
        try:
            # Try to get PDU power data using 'show pdu power'
            power_response = await self.queue_command("show pdu power")
            power_data = parse_pdu_power_data(power_response)

            # If first attempt fails, try with 'show inlets all details'
            if not power_data:
                inlet_response = await self.queue_command("show inlets all details")
                power_data = parse_pdu_power_data(inlet_response)

            # If still no data, try 'show power'
            if not power_data:
                simple_response = await self.queue_command("show power")
                power_data = parse_pdu_power_data(simple_response)

            if power_data:
                # Update sensor values
                self.sensors.update(
                    {
                        SENSOR_PDU_POWER: power_data.get("power"),
                        SENSOR_PDU_CURRENT: power_data.get("current"),
                        SENSOR_PDU_VOLTAGE: power_data.get("voltage"),
                        SENSOR_PDU_ENERGY: power_data.get("energy"),
                        SENSOR_PDU_POWER_FACTOR: power_data.get("power_factor"),
                        SENSOR_PDU_FREQUENCY: power_data.get("frequency"),
                    }
                )

                _LOGGER.debug(
                    "Updated power data: Power=%s W, Current=%s A, Voltage=%s V, Energy=%s Wh, PF=%s, Freq=%s Hz",
                    power_data.get("power"),
                    power_data.get("current"),
                    power_data.get("voltage"),
                    power_data.get("energy"),
                    power_data.get("power_factor"),
                    power_data.get("frequency"),
                )

            # Update outlet-specific power data if this model supports it
            capabilities = self.get_model_capabilities()
            if capabilities.get("has_current_sensing", False):
                await self._update_outlet_power_data()

        except Exception as err:
            _LOGGER.error("Error updating power data: %s", err)
            raise

    async def _update_outlet_power_data(self) -> None:
        """Update power data for each outlet."""
        try:
            # Try to get outlet power data
            for outlet_num in self.outlet_states:
                try:
                    # Command format varies by model, try different formats
                    for cmd_format in [
                        f"show outlet {outlet_num} power",
                        f"show outlet {outlet_num} details",
                        f"show outlets {outlet_num}",
                    ]:
                        try:
                            response = await self.queue_command(cmd_format)
                            power_data = parse_pdu_power_data(response)

                            if power_data:
                                # Store the outlet power readings
                                self.outlet_power[outlet_num] = power_data.get("power")
                                self.outlet_current[outlet_num] = power_data.get(
                                    "current"
                                )
                                self.outlet_voltage[outlet_num] = power_data.get(
                                    "voltage"
                                )
                                self.outlet_energy[outlet_num] = power_data.get(
                                    "energy"
                                )
                                self.outlet_power_factor[outlet_num] = power_data.get(
                                    "power_factor"
                                )
                                self.outlet_line_frequency[outlet_num] = power_data.get(
                                    "frequency"
                                )
                                break

                        except Exception:
                            continue

                except Exception as err:
                    _LOGGER.debug(
                        "Error updating outlet %d power data: %s", outlet_num, err
                    )

        except Exception as err:
            _LOGGER.error("Error updating outlet power data: %s", err)

    async def _update_temperature(self) -> None:
        """Update temperature data from the PDU."""
        try:
            # Try to get temperature data using 'show pdu temperature'
            response = await self.queue_command("show pdu temperature")
            temp_data = parse_pdu_temperature(response)

            # If first attempt fails, try with 'show temperature'
            if not temp_data:
                response = await self.queue_command("show temperature")
                temp_data = parse_pdu_temperature(response)

            if temp_data:
                # Update temperature value
                self.sensors[SENSOR_PDU_TEMPERATURE] = temp_data.get("temperature")

                _LOGGER.debug(
                    "Updated temperature: %s °C", temp_data.get("temperature")
                )

        except Exception as err:
            _LOGGER.error("Error updating temperature: %s", err)
