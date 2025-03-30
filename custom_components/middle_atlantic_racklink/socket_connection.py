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

                # Wait for the initial prompt
                initial_output = await asyncio.wait_for(
                    self._read_until_pattern(b"Username:"), timeout=self.timeout
                )

                _LOGGER.debug("Initial connection output: %s", initial_output)

                # Send username
                _LOGGER.debug("Sending username: %s", self.username)
                await self._send_data(f"{self.username}\r\n")

                # Wait for password prompt
                password_output = await asyncio.wait_for(
                    self._read_until_pattern(b"Password:"), timeout=self.timeout
                )
                _LOGGER.debug("Got password prompt: %s", password_output)

                # Send password
                _LOGGER.debug("Sending password")
                await self._send_data(f"{self.password}\r\n")

                # Wait for command prompt
                auth_response = await asyncio.wait_for(
                    self._read_until_pattern(b"#"), timeout=self.timeout
                )

                _LOGGER.debug("Authentication response RAW: %s", auth_response)

                # Check for authentication errors
                if (
                    "invalid" in auth_response.lower()
                    or "failed" in auth_response.lower()
                ):
                    _LOGGER.error("Authentication failed: %s", auth_response)
                    raise ValueError("Authentication failed")

                # Set as connected
                self._connected = True
                _LOGGER.info("Successfully connected to %s:%d", self.host, self.port)

                # Clear any leftover data in the buffer
                await self._clear_buffer()

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

                    # Send the command
                    _LOGGER.debug("RAW COMMAND >>: %r", command)
                    full_command = f"{command}\r\n"
                    await self._send_data(full_command)

                    # Read the response - first we need to skip any echoed command
                    try:
                        # Wait for the command echo (optional, some devices don't echo)
                        echo = await asyncio.wait_for(
                            self._reader.readuntil(b"\r\n"), timeout=1.0
                        )
                        _LOGGER.debug(
                            "Command echo: %r", echo.decode("utf-8", errors="ignore")
                        )
                    except (asyncio.TimeoutError, asyncio.IncompleteReadError):
                        # It's okay if we don't get the echo
                        _LOGGER.debug("No command echo received")
                        pass

                    # Read the actual response until the prompt character
                    response = await asyncio.wait_for(
                        self._read_until_pattern(b"#"), timeout=self.timeout
                    )

                    # Log the raw response
                    _LOGGER.debug("RAW RESPONSE <<: %r", response)

                    # Clean up the response
                    cleaned_response = self._clean_response(response, command)
                    _LOGGER.debug("CLEANED RESPONSE: %r", cleaned_response)
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

    async def _read_until_pattern(self, pattern: bytes, max_size: int = 8192) -> str:
        """Read from the socket until a pattern is matched."""
        if not self._reader:
            raise ConnectionError("Not connected to device")

        buffer = b""

        while True:
            try:
                chunk = await self._reader.read(1024)
                if not chunk:
                    _LOGGER.warning("Connection closed while reading")
                    self._connected = False
                    break

                buffer += chunk

                # Log what we're receiving for debugging
                _LOGGER.debug(
                    "Received chunk: %r", chunk.decode("utf-8", errors="ignore")
                )

                if pattern in buffer:
                    break

                # Safety check to prevent buffer growth
                if len(buffer) > max_size:
                    _LOGGER.warning(
                        "Buffer size exceeded max_size (%d bytes), truncating", max_size
                    )
                    # Keep the last portion that might contain what we're looking for
                    buffer = buffer[-4096:]

            except Exception as err:
                _LOGGER.error("Error reading from socket: %s", err)
                self._connected = False
                raise

        return buffer.decode("utf-8", errors="ignore")

    async def _clear_buffer(self) -> None:
        """Clear any data in the read buffer."""
        if not self._reader:
            return

        try:
            # Set a small timeout for this operation
            self._reader.set_read_limit(8192)
            while True:
                try:
                    # Try to read with a short timeout
                    chunk = await asyncio.wait_for(self._reader.read(1024), timeout=0.1)
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

        # Remove the command echo from the beginning of the response
        response_lines = response.split("\n")
        cleaned_lines = []
        command_found = False

        for line in response_lines:
            stripped_line = line.strip()

            # Skip lines until we find the echoed command
            if not command_found and (
                first_command_line in stripped_line
                or stripped_line.startswith(first_command_line)
            ):
                command_found = True
                _LOGGER.debug("Found command echo in response: %r", stripped_line)
                continue

            # Skip empty lines at the beginning
            if not command_found and not stripped_line:
                continue

            # Include all remaining lines
            if command_found or stripped_line:
                cleaned_lines.append(line)

        # Join the cleaned lines
        cleaned_response = "\n".join(cleaned_lines)

        # Remove the prompt from the end of the response
        if cleaned_response.endswith("#"):
            cleaned_response = cleaned_response[:-1]

        # Remove any trailing whitespace
        cleaned_response = cleaned_response.strip()

        return cleaned_response
