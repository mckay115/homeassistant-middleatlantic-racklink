"""State management functionality for the RackLink controller."""

# from typing import Any, Dict, List, Optional, Tuple # Unused

import asyncio
import logging
import re
import time

_LOGGER = logging.getLogger(__name__)


class StateMixin:
    """State management for RackLink devices."""

    async def update(self) -> bool:
        """Update PDU state - called periodically by the coordinator."""
        try:
            if not self._connected:
                _LOGGER.debug("Not connected during update, attempting reconnection")
                reconnected = await self._handle_connection_issues()
                if not reconnected:
                    _LOGGER.warning("Failed to reconnect during update")
                    self._available = False
                    return False

            # Get basic device info if we don't have it
            if not self._pdu_model or not self._pdu_serial:
                await self.get_device_info()

            # Update all outlet states efficiently with a single command
            _LOGGER.debug("Updating all outlet states")
            try:
                await self.get_all_outlet_states(force_refresh=True)
            except Exception as e:
                _LOGGER.error("Error updating outlet states: %s", e)

            # Get sensor data
            try:
                await self.get_sensor_values(force_refresh=True)
            except Exception as e:
                _LOGGER.error("Error updating sensor values: %s", e)

            # Update detailed outlet metrics for a subset of outlets
            # to avoid overwhelming the device with too many commands
            if any(
                self.get_model_capabilities().get(feature, False)
                for feature in [
                    "supports_power_monitoring",
                    "supports_energy_monitoring",
                    "has_current_sensing",
                ]
            ):
                await self.get_all_power_data(sample_size=3)

            # Mark successful update
            self._available = True
            self._last_update = time.time()
            return True

        except Exception as e:
            _LOGGER.error("Error in update: %s", e)
            self._available = False
            return False

    async def get_device_info(self) -> dict:
        """Get device information."""
        _LOGGER.debug("Getting device information for %s", self._host)

        if not self._connected:
            _LOGGER.debug("Not connected during get_device_info, attempting to connect")
            if not await self.reconnect():
                _LOGGER.warning("Could not connect to get device info")
                return {}

        # Only fetch device info if we don't have it yet
        if not self._pdu_model or not self._pdu_serial:
            _LOGGER.info("Fetching PDU details")

            try:
                # Import parser module here to avoid circular imports
                from ..parser import (
                    normalize_model_name,
                    parse_device_info,
                    parse_network_info,
                )

                # Get PDU details
                details_cmd = "show pdu details"
                details_response = await self.send_command(details_cmd)

                if details_response:
                    # Use the parser to extract device info
                    device_info = parse_device_info(details_response)

                    # Update internal data from parsed results
                    if "name" in device_info:
                        self._pdu_name = device_info["name"]
                        _LOGGER.debug("Found PDU name: %s", self._pdu_name)

                    if "model" in device_info:
                        raw_model = device_info["model"]
                        self._pdu_model = normalize_model_name(raw_model)
                        _LOGGER.debug(
                            "Found PDU model: %s (normalized from %s)",
                            self._pdu_model,
                            raw_model,
                        )

                    if "serial" in device_info:
                        self._pdu_serial = device_info["serial"]
                        _LOGGER.debug("Found PDU serial: %s", self._pdu_serial)

                    if "firmware" in device_info:
                        self._pdu_firmware = device_info["firmware"]
                        _LOGGER.debug("Found PDU firmware: %s", self._pdu_firmware)

                # Get MAC address if we don't have it
                if not self._mac_address:
                    _LOGGER.debug("Getting network interface information")
                    net_cmd = "show network interface eth1"
                    net_response = await self.send_command(net_cmd)

                    if net_response:
                        network_info = parse_network_info(net_response)
                        if "mac_address" in network_info:
                            self._mac_address = network_info["mac_address"]
                            _LOGGER.debug(
                                "Found PDU MAC address: %s", self._mac_address
                            )
                    else:
                        # Try alternative interface name if eth1 failed
                        net_cmd = "show network interface eth0"
                        net_response = await self.send_command(net_cmd)

                        if net_response:
                            network_info = parse_network_info(net_response)
                            if "mac_address" in network_info:
                                self._mac_address = network_info["mac_address"]
                                _LOGGER.debug(
                                    "Found PDU MAC address: %s", self._mac_address
                                )

            except Exception as e:
                _LOGGER.error("Error getting device info: %s", e)
        else:
            _LOGGER.debug("Using cached device info")

        # Fill in defaults for missing data
        if not self._pdu_model:
            _LOGGER.warning("Could not determine PDU model, using default")
            self._pdu_model = "RLNK-P920R"  # Default model

        if not self._pdu_name:
            self._pdu_name = f"RackLink PDU {self._host}"

        if not self._pdu_serial:
            # Generate a pseudo-serial based on MAC if we have it, otherwise use host
            if self._mac_address:
                self._pdu_serial = f"UNKNOWN-{self._mac_address.replace(':', '')}"
            else:
                self._pdu_serial = f"UNKNOWN-{self._host.replace('.', '')}"

        # Update PDU info dictionary
        self._pdu_info = {
            "model": self._pdu_model or "Unknown",
            "firmware": self._pdu_firmware or "Unknown",
            "serial": self._pdu_serial or "Unknown",
            "name": self._pdu_name or "RackLink PDU",
            "mac_address": self._mac_address or "Unknown",
        }

        _LOGGER.debug("Device info: %s", self._pdu_info)
        return self._pdu_info

    async def get_outlet_state(self, outlet_num: int) -> bool:
        """Get the current state of a specific outlet."""
        if not self._connected:
            _LOGGER.debug("Not connected, cannot get outlet state")
            return False

        try:
            _LOGGER.info("Getting state for outlet %d", outlet_num)
            command = f"show outlets {outlet_num} details"
            _LOGGER.debug("Sending command to get outlet state: %s", command)
            response = await self.send_command(command)

            if not response:
                _LOGGER.error(
                    "No response getting outlet state for outlet %d", outlet_num
                )
                return await self.get_direct_outlet_state(outlet_num)

            # Check if we got an unrecognized argument error
            if "unrecognized" in response.lower() or "invalid" in response.lower():
                _LOGGER.warning("Command not recognized, trying alternative approach")
                return await self.get_direct_outlet_state(outlet_num)

            # Log response length for debugging
            _LOGGER.debug(
                "Received response of length %d for outlet %d",
                len(response),
                outlet_num,
            )

            # Import parser here to avoid circular imports
            from ..parser import parse_outlet_state

            # Use the robust parser function
            state = parse_outlet_state(response, outlet_num)

            if state is not None:
                # Update our internal state
                self._outlet_states[outlet_num] = state
                _LOGGER.info(
                    "Outlet %d state determined successfully: %s",
                    outlet_num,
                    "ON" if state else "OFF",
                )
                return state
            else:
                _LOGGER.error(
                    "Could not parse outlet state from response for outlet %d - trying direct method",
                    outlet_num,
                )
                # Try alternative approach
                return await self.get_direct_outlet_state(outlet_num)

        except Exception as e:
            _LOGGER.error("Error getting outlet state for outlet %d: %s", outlet_num, e)
            return self._outlet_states.get(outlet_num, False)

    async def get_direct_outlet_state(self, outlet_num: int) -> bool:
        """Get the outlet state using direct power status command."""
        if not self._connected:
            return False

        try:
            _LOGGER.debug("Using direct method to get state for outlet %d", outlet_num)
            commands_to_try = [
                f"power outlets status {outlet_num}",
                f"power outletgroup {outlet_num} status",
                f"power outlet {outlet_num} status",
            ]

            for cmd in commands_to_try:
                _LOGGER.debug("Trying command: %s", cmd)
                response = await self.send_command(cmd)

                # Check if command was recognized
                if response and "unknown command" not in response.lower():
                    # Use the parser to extract state
                    from ..parser import parse_outlet_state

                    state = parse_outlet_state(response, outlet_num)

                    if state is not None:
                        self._outlet_states[outlet_num] = state
                        return state

            # Fall back to cached state or default to OFF
            _LOGGER.warning(
                "Could not determine outlet %d state using direct methods", outlet_num
            )
            return self._outlet_states.get(outlet_num, False)

        except Exception as e:
            _LOGGER.error("Error in get_direct_outlet_state: %s", e)
            return self._outlet_states.get(outlet_num, False)

    async def get_all_outlet_states(self, force_refresh: bool = False) -> dict:
        """Get states of all outlets."""
        if not self._connected:
            _LOGGER.warning("Not connected, cannot get outlet states")
            return self._outlet_states

        if not force_refresh and self._outlet_states:
            # Return cached states if available
            return self._outlet_states

        _LOGGER.debug("Getting all outlet states")

        try:
            # Try multiple command formats to get all outlet states at once
            commands_to_try = [
                "power outlets status",
                "power outletgroup status",
                "power outlet status",
                "power status",
            ]

            response = None
            command_worked = False

            for cmd in commands_to_try:
                _LOGGER.debug("Trying command: %s", cmd)
                response = await self.send_command(cmd)

                if (
                    response
                    and "Unknown command" not in response
                    and "error" not in response.lower()
                ):
                    _LOGGER.info("Command '%s' accepted by device", cmd)
                    command_worked = True
                    break

            if not command_worked:
                _LOGGER.warning(
                    "All outlet status commands failed, trying alternatives"
                )
                # Try fallback approach - get states individually
                await self._get_outlet_states_individually()
                return self._outlet_states

            # Use the parser to extract outlet states
            from ..parser import parse_all_outlet_states

            parsed_states = parse_all_outlet_states(response)

            if parsed_states:
                _LOGGER.info("Found %d outlet states using parser", len(parsed_states))
                self._outlet_states.update(parsed_states)
                return self._outlet_states
            else:
                _LOGGER.warning(
                    "Parser could not find outlet states, falling back to individual queries"
                )
                await self._get_outlet_states_individually()
                return self._outlet_states

        except Exception as e:
            _LOGGER.error("Error getting all outlet states: %s", e)

        return self._outlet_states

    async def _get_outlet_states_individually(self):
        """Get outlet states one by one as a fallback method."""
        _LOGGER.debug("Getting outlet states individually")

        capabilities = self.get_model_capabilities()
        num_outlets = capabilities.get("num_outlets", 8)

        for outlet_num in range(1, num_outlets + 1):
            try:
                # Get state for this specific outlet
                state = await self.get_outlet_state(outlet_num)
                self._outlet_states[outlet_num] = state

                # Brief delay to avoid overwhelming the device
                await asyncio.sleep(0.2)

            except Exception as e:
                _LOGGER.error("Error getting state for outlet %d: %s", outlet_num, e)
                # Assume OFF in case of error
                self._outlet_states[outlet_num] = False

    async def get_outlet_power_data(self, outlet_num: int) -> dict:
        """Get power data for a specific outlet."""
        if not self._connected:
            _LOGGER.warning("Not connected, cannot get outlet power data")
            return {}

        try:
            _LOGGER.debug("Getting power data for outlet %d", outlet_num)

            # Try standard command format
            cmd = f"power outlets status {outlet_num}"
            response = await self.send_command(cmd)

            if not response or "Unknown command" in response.lower():
                _LOGGER.warning(
                    "Could not get power data for outlet %d: %s", outlet_num, response
                )
                return {}

            # Import parser here to avoid circular imports
            from ..parser import parse_outlet_details

            # Parse the data
            outlet_data = parse_outlet_details(response, outlet_num)

            # Update the internal data structures
            if "current" in outlet_data:
                self._outlet_current[outlet_num] = outlet_data["current"]
            if "voltage" in outlet_data:
                self._outlet_voltage[outlet_num] = outlet_data["voltage"]
            if "power" in outlet_data:
                self._outlet_power[outlet_num] = outlet_data["power"]
            if "energy" in outlet_data:
                self._outlet_energy[outlet_num] = outlet_data["energy"]
            if "power_factor" in outlet_data:
                self._outlet_power_factor[outlet_num] = outlet_data["power_factor"]
            if "apparent_power" in outlet_data:
                self._outlet_apparent_power[outlet_num] = outlet_data["apparent_power"]
            if "line_frequency" in outlet_data:
                self._outlet_line_frequency[outlet_num] = outlet_data["line_frequency"]
            if "non_critical" in outlet_data:
                self._outlet_non_critical[outlet_num] = outlet_data["non_critical"]

            return outlet_data
        except Exception as e:
            _LOGGER.error("Error getting outlet power data: %s", e)
            return {}

    async def get_all_power_data(self, sample_size: int = 3) -> bool:
        """Get power data for all outlets efficiently.

        Uses sampling to avoid overwhelming the device, fetching detailed data
        for a subset of outlets during each update cycle.
        """
        if not self._connected:
            _LOGGER.debug("Not connected, skipping power data refresh")
            return False

        try:
            # Get model capabilities
            capabilities = self.get_model_capabilities()
            num_outlets = capabilities.get("num_outlets", 8)

            # Determine which outlets to sample in this cycle
            all_outlets = list(range(1, num_outlets + 1))

            # Try to select outlets that don't have power data yet
            missing_power_data = [o for o in all_outlets if o not in self._outlet_power]

            # Decide which outlets to sample
            if len(missing_power_data) > 0:
                # Prioritize outlets missing data
                sample_outlets = missing_power_data[:sample_size]
            else:
                # Cycle through all outlets over time
                current_time = time.time()
                # Use time-based sampling to ensure all outlets get updated eventually
                start_idx = (
                    int(current_time / 30) % num_outlets
                )  # Rotate every 30 seconds
                indices = [(start_idx + i) % num_outlets for i in range(sample_size)]
                sample_outlets = [all_outlets[i] for i in indices]

            _LOGGER.debug("Sampling power data for outlets: %s", sample_outlets)

            success_count = 0
            for outlet in sample_outlets:
                try:
                    data = await self.get_outlet_power_data(outlet)
                    if data:
                        success_count += 1
                    # Brief delay to avoid overwhelming the device
                    await asyncio.sleep(0.2)
                except Exception as e:
                    _LOGGER.error(
                        "Error getting power data for outlet %d: %s", outlet, e
                    )

            _LOGGER.debug(
                "Got power data for %d of %d sampled outlets",
                success_count,
                len(sample_outlets),
            )

            return success_count > 0

        except Exception as e:
            _LOGGER.error("Error getting power data: %s", e)
            return False

    async def get_outlet_name(self, outlet_num: int) -> str:
        """Get the name of a specific outlet."""
        # If we already have the name cached, use it
        if outlet_num in self._outlet_names:
            return self._outlet_names[outlet_num]

        # Otherwise, try to get it from the device
        try:
            if not self._connected:
                await self._handle_connection_issues()
                if not self._connected:
                    return f"Outlet {outlet_num}"

            # Get outlet details
            command = f"show outlets {outlet_num} details"
            response = await self.send_command(command)

            if not response:
                return f"Outlet {outlet_num}"

            # Try to parse the outlet name
            # Look for the outlet header line which includes the name
            name_match = re.search(rf"Outlet {outlet_num}(?: - (.+?))?:", response)

            if name_match and name_match.group(1):
                name = name_match.group(1).strip()
                self._outlet_names[outlet_num] = name
                return name
            else:
                return f"Outlet {outlet_num}"

        except Exception as e:
            _LOGGER.error("Error getting outlet name: %s", e)
            return f"Outlet {outlet_num}"

    async def is_outlet_available(self, outlet_num: int) -> bool:
        """Check if an outlet exists and is available on this PDU."""
        if not self._connected:
            return False

        capabilities = self.get_model_capabilities()
        num_outlets = capabilities.get("num_outlets", 8)

        # Check if outlet number is valid
        if outlet_num < 1 or outlet_num > num_outlets:
            return False

        # If we have data for this outlet, it's available
        if outlet_num in self._outlet_states:
            return True

        # Try to get details for this outlet
        try:
            command = f"show outlets {outlet_num} details"
            response = await self.send_command(command)

            # If we get a valid response that includes the outlet number,
            # then it exists on this PDU
            if response and f"Outlet {outlet_num}" in response:
                return True
            else:
                return False
        except Exception:
            return False

    async def get_sensor_values(self, force_refresh: bool = False) -> dict:
        """Get sensor values from the PDU."""
        if not self._connected:
            _LOGGER.warning("Not connected, cannot get sensor values")
            return self._sensors

        # If sensor data is cached and we don't need to refresh, return it
        if self._sensors and not force_refresh:
            return self._sensors

        try:
            _LOGGER.debug("Getting sensor values")
            sensor_data = {}

            # Get temperature data
            try:
                from ..parser import parse_pdu_temperature

                temp_cmd = "show pdu temperature"
                temp_response = await self.send_command(temp_cmd)

                if temp_response:
                    temperature = parse_pdu_temperature(temp_response)
                    if temperature is not None:
                        sensor_data["temperature"] = temperature
                        _LOGGER.debug("Found temperature: %s", temperature)
            except Exception as e:
                _LOGGER.error("Error getting temperature data: %s", e)

            # Get power data for the whole PDU
            try:
                from ..parser import parse_pdu_power_data

                # Try different commands to get power data
                cmds_to_try = [
                    "show pdu power",
                    "show inlets all details",
                    "power status",
                ]

                for cmd in cmds_to_try:
                    power_response = await self.send_command(cmd)
                    if (
                        power_response
                        and "unknown command" not in power_response.lower()
                    ):
                        power_data = parse_pdu_power_data(power_response)

                        if power_data:
                            sensor_data.update(power_data)
                            _LOGGER.debug(
                                "Found PDU power data from %s: %s", cmd, power_data
                            )
                            break
            except Exception as e:
                _LOGGER.error("Error getting PDU power data: %s", e)

            # Update our sensors dict
            self._sensors = sensor_data
            return self._sensors

        except Exception as e:
            _LOGGER.error("Error getting sensor values: %s", e)
            return self._sensors
