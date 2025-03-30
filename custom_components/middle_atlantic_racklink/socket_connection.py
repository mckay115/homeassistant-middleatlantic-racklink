"""Socket connection handler for Middle Atlantic RackLink devices."""

import asyncio
import logging
import re
import time
from typing import Optional

_LOGGER = logging.getLogger(__name__)


class SocketConnection:
    """Socket connection manager for Middle Atlantic RackLink devices."""

    def __init__(
        self,
        host: str,
        port: int,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: int = 20,
    ) -> None:
        """Initialize the socket connection.

        Args:
            host: Hostname or IP address of the device
            port: Port number for connection
            username: Username for authentication
            password: Password for authentication
            timeout: Timeout in seconds for connections and commands
        """
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout
        self._reader = None
        self._writer = None
        self._connected = False
        self._connection_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()  # Lock to prevent concurrent commands

    async def connect(self) -> bool:
        """Connect to the device and authenticate if needed."""
        async with self._connection_lock:
            if self._connected and self._writer and not self._writer.is_closing():
                _LOGGER.debug("Already connected")
                return True

            try:
                _LOGGER.info("Connecting to %s:%d", self.host, self.port)

                # Open the socket connection
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port),
                    timeout=self.timeout,
                )

                # Wait for the initial prompt to stabilize the connection
                initial_data = await asyncio.wait_for(
                    self._reader.read(1024), timeout=5.0
                )
                _LOGGER.debug(
                    "Initial connection output: %r",
                    initial_data.decode("utf-8", errors="ignore"),
                )

                # Wait briefly to make sure the connection is stable
                await asyncio.sleep(1)

                # Authenticate if needed
                if self.username and self.password:
                    # Send username
                    _LOGGER.debug("Sending username: %s", self.username)
                    await self._send_data(f"{self.username}\r\n")
                    await asyncio.sleep(0.5)  # Wait for device to process

                    # Wait for password prompt
                    password_prompt = await asyncio.wait_for(
                        self._reader.read(1024), timeout=5.0
                    )
                    _LOGGER.debug(
                        "Password prompt: %r",
                        password_prompt.decode("utf-8", errors="ignore"),
                    )

                    # Send password
                    _LOGGER.debug("Sending password")
                    await self._send_data(f"{self.password}\r\n")
                    await asyncio.sleep(0.5)  # Wait for device to process

                    # Wait for command prompt
                    auth_response = await asyncio.wait_for(
                        self._reader.read(1024), timeout=5.0
                    )
                    _LOGGER.debug(
                        "Authentication response: %r",
                        auth_response.decode("utf-8", errors="ignore"),
                    )

                    # Check for authentication errors
                    decoded_response = auth_response.decode(
                        "utf-8", errors="ignore"
                    ).lower()
                    if "invalid" in decoded_response or "failed" in decoded_response:
                        _LOGGER.error("Authentication failed: %r", auth_response)
                        raise ValueError("Authentication failed")

                # Clear buffer once more
                await self._clear_buffer()

                # Send a newline to get to a clean prompt
                await self._send_data("\r\n")
                await asyncio.sleep(0.5)

                # Clear any leftover data in the buffer
                await self._clear_buffer()

                # Set as connected
                self._connected = True
                _LOGGER.info("Successfully connected to %s:%d", self.host, self.port)
                return True

            except asyncio.TimeoutError as err:
                _LOGGER.error(
                    "Timeout connecting to %s:%d: %s", self.host, self.port, err
                )
                await self._cleanup_connection()
                return False
            except Exception as err:
                _LOGGER.error(
                    "Error connecting to %s:%d: %s", self.host, self.port, err
                )
                await self._cleanup_connection()
                return False

    async def _cleanup_connection(self) -> None:
        """Clean up the connection resources."""
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception as err:
                _LOGGER.debug("Error during connection cleanup: %s", err)

        self._reader = None
        self._writer = None
        self._connected = False

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        async with self._connection_lock:
            await self._cleanup_connection()
            _LOGGER.debug("Disconnected from %s:%d", self.host, self.port)

    async def send_command(self, command: str) -> str:
        """Send a command to the device and wait for a response."""
        # Use a lock to prevent multiple commands being sent at the same time
        async with self._command_lock:
            retry_count = 0
            max_retries = 2

            while retry_count < max_retries:
                if not self._connected or not self._writer or not self._reader:
                    _LOGGER.debug(
                        "Not connected, attempting to connect before sending command"
                    )
                    if not await self.connect():
                        raise ConnectionError(
                            "Not connected to device and reconnection failed"
                        )

                try:
                    # Check if the writer is closing or closed
                    if self._writer.is_closing():
                        _LOGGER.debug("Writer is closing, attempting to reconnect")
                        await self._cleanup_connection()
                        if not await self.connect():
                            raise ConnectionError(
                                "Connection closed and reconnection failed"
                            )

                    # First clear the buffer to ensure we start with a clean state
                    await self._clear_buffer()

                    # Log the exact command we're about to send
                    _LOGGER.debug("RAW COMMAND >>: %r", command)

                    # Format and send the full command, ensuring proper line endings
                    # Make sure to append proper line ending
                    cmd_line = command.strip()

                    # Send the command with proper line endings
                    full_command = f"{cmd_line}\r\n"
                    await self._send_data(full_command)

                    # Short wait to ensure command is processed
                    await asyncio.sleep(0.1)

                    # Start reading the response
                    buffer = b""
                    start_time = time.time()

                    # Wait for the prompt character to indicate completion
                    while time.time() - start_time < self.timeout:
                        try:
                            chunk = await asyncio.wait_for(
                                self._reader.read(1024), timeout=1.0
                            )

                            if not chunk:
                                _LOGGER.warning(
                                    "Connection closed while reading response"
                                )
                                self._connected = False
                                break

                            buffer += chunk
                            decoded = chunk.decode("utf-8", errors="ignore")
                            _LOGGER.debug("Response chunk: %r", decoded)

                            # If we see the prompt or a long pause, we're done
                            if (
                                "#" in decoded
                                or ">" in decoded
                                or decoded.endswith("]")
                            ):
                                _LOGGER.debug(
                                    "Found prompt character, response complete"
                                )
                                break

                            # If we've gotten a large amount of data, check for error patterns
                            if len(buffer) > 4096:
                                _LOGGER.debug(
                                    "Received large buffer, checking if response is complete"
                                )
                                if "#" in buffer.decode(
                                    "utf-8", errors="ignore"
                                ) or ">" in buffer.decode("utf-8", errors="ignore"):
                                    break

                        except asyncio.TimeoutError:
                            # If we've been waiting a while with no new data, we're probably done
                            _LOGGER.debug(
                                "Timeout reading chunk, response might be complete"
                            )
                            break

                    # Decode the full response
                    response = buffer.decode("utf-8", errors="ignore")
                    _LOGGER.debug("RAW RESPONSE <<: %r", response)

                    # Clean up the response
                    cleaned_response = self._clean_response(response, command)
                    _LOGGER.debug("CLEANED RESPONSE: %r", cleaned_response)

                    # Check for "unknown command" responses
                    if (
                        "unknown command" in cleaned_response.lower()
                        and retry_count < max_retries - 1
                    ):
                        _LOGGER.warning(
                            "Unknown command response detected, retrying with full command"
                        )
                        retry_count += 1
                        await asyncio.sleep(1)
                        continue

                    return cleaned_response

                except asyncio.TimeoutError:
                    _LOGGER.warning(
                        "Timeout waiting for response to command: %r", command
                    )
                    retry_count += 1
                    await self._cleanup_connection()
                    if retry_count < max_retries:
                        _LOGGER.debug(
                            "Retrying command after timeout (attempt %d/%d)",
                            retry_count + 1,
                            max_retries,
                        )
                        await asyncio.sleep(1)  # Brief delay before retry
                    else:
                        raise TimeoutError(
                            f"Command timed out after {self.timeout}s: {command}"
                        )
                except Exception as err:
                    _LOGGER.error("Error sending command '%r': %s", command, err)
                    await self._cleanup_connection()
                    retry_count += 1
                    if retry_count < max_retries:
                        _LOGGER.debug(
                            "Retrying command after error (attempt %d/%d)",
                            retry_count + 1,
                            max_retries,
                        )
                        await asyncio.sleep(1)  # Brief delay before retry
                    else:
                        raise

            # This should not be reached due to the exception in the last iteration
            raise ConnectionError(
                f"Failed to send command after {max_retries} attempts"
            )

    async def _send_data(self, data: str) -> None:
        """Send data to the device."""
        if not self._writer:
            raise ConnectionError("Not connected to device")

        try:
            _LOGGER.debug("Sending data: %r", data)
            self._writer.write(data.encode())
            await self._writer.drain()
        except Exception as err:
            _LOGGER.error("Error sending data: %s", err)
            self._connected = False
            raise ConnectionError(f"Error sending data: {err}")

    async def _clear_buffer(self) -> None:
        """Clear any data in the read buffer."""
        if not self._reader:
            return

        try:
            # Set a small timeout for this operation
            while True:
                try:
                    # Try to read with a short timeout
                    chunk = await asyncio.wait_for(self._reader.read(1024), timeout=0.2)
                    if not chunk:
                        break
                    _LOGGER.debug(
                        "Cleared buffer data: %r",
                        chunk.decode("utf-8", errors="ignore"),
                    )
                except asyncio.TimeoutError:
                    # No more data to read
                    break
        except Exception as err:
            _LOGGER.debug("Error clearing buffer: %s", err)

    def _clean_response(self, response: str, command: str) -> str:
        """Clean the response from the device."""
        if not response:
            return ""

        # If the command is a multi-line command, only use the first line for matching
        first_command_line = command.split("\n")[0] if "\n" in command else command

        # Create a list of lines from the response, being careful about different line endings
        response_lines = re.split(r"\r\n|\r|\n", response)
        cleaned_lines = []
        command_found = False
        prompt_found = False
        syntax_error_found = False

        # Look for lines that we want to keep
        for line in response_lines:
            line = line.rstrip()

            # Skip empty lines
            if not line:
                continue

            # Skip the echoed command
            if not command_found and first_command_line in line:
                command_found = True
                _LOGGER.debug("Skipped command echo: %r", line)
                continue

            # Stop when we hit the prompt
            if line.endswith("#") or line.endswith(">") or "]" in line:
                prompt_found = True
                _LOGGER.debug("Found prompt, stopping: %r", line)
                break

            # Skip 'unknown command' message lines
            if "unknown command" in line.lower():
                _LOGGER.debug("Detected 'unknown command' message: %r", line)
                continue

            # Skip command help menus
            if "available commands:" in line.lower():
                _LOGGER.debug("Skipping command help menu")
                break

            # Skip command error response lines - pattern seen in the logs
            if (
                line.strip().startswith("^")
                or "label  Outlet label" in line
                or "Outlet label (or 'all')" in line
            ):
                _LOGGER.debug("Detected syntax error response: %r", line)
                syntax_error_found = True
                continue

            # Skip command syntax lines (often start with carets)
            if line.startswith("^"):
                _LOGGER.debug("Skipping command syntax line: %r", line)
                continue

            # Keep this line
            cleaned_lines.append(line)

        # Join all the cleaned lines
        cleaned_response = "\n".join(cleaned_lines)

        # If we found a syntax error and have no content, make it explicit
        if syntax_error_found and not cleaned_lines:
            _LOGGER.debug(
                "Command had syntax error, returning empty response with marker"
            )
            return "ERROR: Command syntax error"

        # If we didn't find any useful content, but we got "unknown command"
        if not cleaned_lines and (
            "unknown command" in response.lower()
            or "available commands:" in response.lower()
        ):
            _LOGGER.warning("Command not recognized by device: %r", command)
            return "ERROR: Command not recognized"

        return cleaned_response
