"""Command execution functionality for the RackLink controller."""

from typing import List

import asyncio
import logging
import re

_LOGGER = logging.getLogger(__name__)


class CommandsMixin:
    """Commands mixin for RackLink devices."""

    def _ensure_command_processor_running(self) -> None:
        """Ensure the command processor task is running."""
        if self._command_processor_task is None or self._command_processor_task.done():
            _LOGGER.debug("Starting command processor task")
            self._command_processor_task = asyncio.create_task(
                self._process_command_queue()
            )
            # Add a done callback to log when the task completes
            self._command_processor_task.add_done_callback(
                lambda fut: _LOGGER.debug(
                    "Command processor task completed: %s",
                    (
                        fut.exception()
                        if fut.done() and not fut.cancelled()
                        else "No exception"
                    ),
                )
            )

    async def _process_command_queue(self):
        """Process commands from the queue."""
        _LOGGER.debug("Starting command queue processor")
        consecutive_errors = 0

        while not self._shutdown_requested:
            try:
                # Check if we're connected
                if not self._connected:
                    reconnect_success = await self._handle_connection_issues()
                    if not reconnect_success:
                        # Wait longer if we couldn't reconnect
                        await asyncio.sleep(5)
                        continue
                    else:
                        # Successfully reconnected, reset error counter
                        consecutive_errors = 0

                # Try to get a command from the queue with a timeout
                try:
                    command_item = await asyncio.wait_for(
                        self._command_queue.get(), timeout=0.5
                    )
                except asyncio.TimeoutError:
                    # No commands in queue, continue the loop
                    await asyncio.sleep(0.1)
                    continue

                command, future = command_item
                _LOGGER.debug("Processing command from queue: %s", command)

                # Send the command and get response
                try:
                    # Try to send the command
                    response = await self.send_command(command)
                    consecutive_errors = 0  # Reset on success

                    # Set the result if we have a future
                    if future is not None and not future.done():
                        future.set_result(response)
                except Exception as e:
                    _LOGGER.error("Error executing queued command '%s': %s", command, e)
                    consecutive_errors += 1

                    # Set exception on future if we have one
                    if future is not None and not future.done():
                        future.set_exception(e)

                    # If we have multiple errors in a row, try reconnecting
                    if consecutive_errors >= 3:
                        _LOGGER.warning(
                            "Multiple consecutive command errors, attempting reconnect"
                        )
                        await self._handle_connection_issues()
                        consecutive_errors = 0
                finally:
                    # Mark task as done regardless of outcome
                    self._command_queue.task_done()

                # Small delay to prevent flooding the device
                # Use a longer delay after power commands
                if "power outlets" in command:
                    await asyncio.sleep(self._command_delay * 2)
                else:
                    await asyncio.sleep(self._command_delay)

            except asyncio.CancelledError:
                _LOGGER.debug("Command processor was cancelled")
                break
            except Exception as e:
                _LOGGER.error("Unexpected error in command processor: %s", e)
                consecutive_errors += 1

                # Try to reconnect if we have multiple errors
                if consecutive_errors >= 3:
                    await self._handle_connection_issues()
                    consecutive_errors = 0

                await asyncio.sleep(
                    1
                )  # Sleep briefly to avoid tight loop on persistent errors

        _LOGGER.debug(
            "Command processor exiting, shutdown requested: %s",
            self._shutdown_requested,
        )

        # If we're not shutting down, restart the processor
        if not self._shutdown_requested:
            _LOGGER.debug("Restarting command processor")
            self._command_processor_task = asyncio.create_task(
                self._process_command_queue()
            )

    def _map_standard_command(self, command: str) -> str:
        """Map a standard command to one that works with this device."""
        # First, check exact matches for standard commands
        if command == "show outlets all":
            return "power outlets status"

        # Handle show outlet details command for specific outlet
        if re.match(r"show outlets? (\d+) details?", command):
            outlet_num = re.match(r"show outlets? (\d+) details?", command).group(1)
            return f"power outlets status {outlet_num}"

        # Handle power commands with different syntax
        power_off_match = re.match(r"power outlets? (\d+) off", command)
        if power_off_match:
            outlet_num = power_off_match.group(1)
            return f"power outlets off {outlet_num}"

        power_on_match = re.match(r"power outlets? (\d+) on", command)
        if power_on_match:
            outlet_num = power_on_match.group(1)
            return f"power outlets on {outlet_num}"

        cycle_match = re.match(r"power outlets? (\d+) cycle", command)
        if cycle_match:
            outlet_num = cycle_match.group(1)
            return f"power outlets cycle {outlet_num}"

        # All outlets commands
        if command == "power outlets all on":
            return "power outlets on all"

        if command == "power outlets all off":
            return "power outlets off all"

        if command == "power outlets all cycle":
            return "power outlets cycle all"

        # Try outletgroup commands seen in error logs
        if re.match(r"power outlets? (\d+) off", command):
            outlet_num = re.match(r"power outlets? (\d+) off", command).group(1)
            return f"power outletgroup {outlet_num} off"

        if re.match(r"power outlets? (\d+) on", command):
            outlet_num = re.match(r"power outlets? (\d+) on", command).group(1)
            return f"power outletgroup {outlet_num} on"

        if re.match(r"power outlets? status (\d+)", command):
            outlet_num = re.match(r"power outlets? status (\d+)", command).group(1)
            return f"power outletgroup {outlet_num} status"

        # Handle show inlets command
        if command == "show inlets all details":
            alternative_cmds = [
                "show inlets all details",
                "show inlet details",
                "show pdu power",
            ]

            # Return the first command as default, will try alternatives in send_command
            return alternative_cmds[0]

        # Return original command if no mapping found
        return command

    async def send_command(
        self,
        command: str,
        timeout: int = None,
        wait_time: float = 0.5,
        retry_alternative_command: bool = True,
    ) -> str:
        """Send a command to the device and wait for the response."""
        if not command:
            return ""

        # Map standard command to device-specific format if needed
        mapped_command = self._map_standard_command(command)
        if mapped_command != command:
            _LOGGER.debug(
                "Using mapped command '%s' instead of '%s'", mapped_command, command
            )
            command = mapped_command

        command_timeout = timeout or self._command_timeout
        command_with_newline = f"{command}\r\n"

        # Retry counter for this specific command
        retry_count = 0
        max_retries = 2  # Maximum retries for a single command

        while retry_count <= max_retries:
            try:
                if not self._connected:
                    _LOGGER.debug("Not connected while sending command: %s", command)
                    # Only try to reconnect on the first attempt
                    if retry_count == 0:
                        connection_success = await self._handle_connection_issues()
                        if not connection_success:
                            _LOGGER.warning(
                                "Could not reconnect to send command: %s", command
                            )
                            return ""
                    else:
                        # Don't try to reconnect on subsequent retries
                        return ""

                _LOGGER.debug(
                    "Sending command: %s (timeout: %s, attempt: %d)",
                    command,
                    command_timeout,
                    retry_count + 1,
                )

                # Flush any data in the buffer before sending
                if self._receive_buffer:
                    _LOGGER.debug(
                        "Clearing read buffer of %d bytes before sending command",
                        len(self._receive_buffer),
                    )
                    self._receive_buffer = b""

                # Send the command
                try:
                    await self._socket_write(command_with_newline.encode())
                except ConnectionError as e:
                    _LOGGER.error("Connection error while sending command: %s", e)
                    # Mark as disconnected
                    self._connected = False
                    self._available = False
                    # Try again if we have retries left
                    retry_count += 1
                    continue

                # Wait for device to process the command
                # Some commands need more time, especially power control commands
                cmd_wait_time = wait_time
                if "power outlets" in command:
                    # Power commands need more time
                    cmd_wait_time = wait_time * 2

                await asyncio.sleep(cmd_wait_time)

                # Read response until we get the command prompt
                response = await self._socket_read_until(b"#", timeout=command_timeout)

                # If we lost connection during read, try again
                if not self._connected and retry_count < max_retries:
                    _LOGGER.warning("Lost connection while reading response, retrying")
                    retry_count += 1
                    continue

                # Convert to string for parsing
                response_text = response.decode("utf-8", errors="ignore")

                # Debug log the response
                if len(response_text) < 200:  # Only log small responses
                    _LOGGER.debug("Response for '%s': %s", command, response_text)
                else:
                    _LOGGER.debug(
                        "Response for '%s': %d bytes (showing first 100): %s...",
                        command,
                        len(response_text),
                        response_text[:100],
                    )

                # Check if the response contains an error about unrecognized command
                if (
                    "unrecognized" in response_text.lower()
                    or "invalid" in response_text.lower()
                ) and "argument" in response_text.lower():
                    _LOGGER.warning(
                        "Command '%s' not recognized by device: %s",
                        command,
                        response_text.split("\n")[0],
                    )

                    # If this is our first try and we're allowed to try alternative commands
                    if retry_count == 0 and retry_alternative_command:
                        # If we haven't discovered commands yet, do it now
                        if (
                            not hasattr(self, "_valid_commands")
                            or not self._valid_commands
                        ):
                            _LOGGER.info(
                                "Initiating command discovery to find valid syntax"
                            )
                            await self.discover_valid_commands()
                            # Try again with discovered commands
                            retry_count += 1
                            continue

                # Verify the response contains evidence of command execution
                # For safety, ensure we have a valid response before proceeding
                if not response_text or (
                    len(response_text) < 3 and "#" not in response_text
                ):
                    _LOGGER.warning(
                        "Potentially invalid response: '%s' for command: %s",
                        response_text,
                        command,
                    )

                    if retry_count < max_retries:
                        _LOGGER.debug(
                            "Retrying command: %s (attempt %d)",
                            command,
                            retry_count + 2,
                        )
                        retry_count += 1
                        # Wait before retrying
                        await asyncio.sleep(1)
                        continue

                # If we got here, we have a valid response or have exhausted retries
                return response_text

            except ConnectionError as e:
                _LOGGER.error("Connection error sending command '%s': %s", command, e)
                # Try to recover the connection only on first attempt
                if retry_count == 0:
                    await self._handle_connection_issues()
                retry_count += 1
                if retry_count <= max_retries:
                    await asyncio.sleep(1)  # Wait before retry
                    continue
                return ""

            except asyncio.TimeoutError:
                _LOGGER.error("Timeout sending command '%s'", command)
                retry_count += 1
                if retry_count <= max_retries:
                    await asyncio.sleep(1)  # Wait before retry
                    continue
                return ""

            except Exception as e:
                _LOGGER.error("Error sending command '%s': %s", command, e)
                retry_count += 1
                if retry_count <= max_retries:
                    await asyncio.sleep(1)  # Wait before retry
                    continue
                return ""

        # If we got here, we've exhausted all retries
        return ""

    async def queue_command(self, command: str) -> str:
        """Queue a command to be executed by the command processor.

        This helps prevent overwhelming the device with too many commands at once.
        """
        if not self._connected:
            _LOGGER.warning("Not connected, cannot queue command: %s", command)
            return ""

        # Create a future to receive the result
        future = asyncio.get_running_loop().create_future()

        # Add command and future to the queue
        await self._command_queue.put((command, future))

        try:
            # Wait for the result with a timeout
            return await asyncio.wait_for(future, timeout=self._command_timeout * 2)
        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout waiting for queued command result: %s", command)
            return ""
        except Exception as e:
            _LOGGER.error(
                "Error waiting for queued command result: %s - %s", command, e
            )
            return ""

    async def discover_valid_commands(self) -> List[str]:
        """Discover valid commands from the device help output."""
        _LOGGER.debug("Discovering valid commands")

        if not self._connected:
            await self._handle_connection_issues()
            if not self._connected:
                _LOGGER.warning("Not connected, cannot discover commands")
                return []

        try:
            # Get basic help output
            help_cmd = "help"
            help_output = await self.send_command(help_cmd)

            if not help_output:
                _LOGGER.warning("No help output received, cannot discover commands")
                return []

            # Use parser to extract available commands
            from ..parser import parse_available_commands

            commands = parse_available_commands(help_output)
            self._valid_commands = commands

            _LOGGER.info("Discovered %d valid commands", len(commands))
            return commands

        except Exception as e:
            _LOGGER.error("Error discovering valid commands: %s", e)
            return []

    async def _load_initial_data(self):
        """Load initial data from the PDU."""
        _LOGGER.debug("Loading initial PDU data")
        try:
            # Get PDU information first
            await self.get_device_info()

            # Get basic help to understand available commands
            help_cmd = "help"
            help_response = await self.send_command(help_cmd)
            _LOGGER.debug("Help response: %s", help_response[:500])

            # Get power help for more specific command syntax
            power_help_cmd = "help power"
            power_help_response = await self.send_command(power_help_cmd)
            _LOGGER.debug("Power help response: %s", power_help_response[:500])

            # Try to discover valid commands for this device
            await self.discover_valid_commands()

            # Get additional data
            await self.get_all_outlet_states()
            await self.get_sensor_values()

        except Exception as e:
            _LOGGER.error("Error loading initial data: %s", e)
            return False

        return True
