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

    async def connect(self) -> bool:
        """Connect to the device and authenticate if needed."""
        async with self._connection_lock:
            if self._connected and self._writer and not self._writer.is_closing():
                return True

            try:
                _LOGGER.debug("Connecting to %s:%d", self.host, self.port)

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
                await asyncio.wait_for(
                    self._read_until_pattern(b"Password:"), timeout=self.timeout
                )

                # Send password
                _LOGGER.debug("Sending password")
                await self._send_data(f"{self.password}\r\n")

                # Wait for command prompt
                auth_response = await asyncio.wait_for(
                    self._read_until_pattern(b"#"), timeout=self.timeout
                )

                _LOGGER.debug("Authentication response: %s", auth_response)

                # Check for authentication errors
                if (
                    "invalid" in auth_response.lower()
                    or "failed" in auth_response.lower()
                ):
                    raise ValueError("Authentication failed")

                # Set as connected
                self._connected = True
                _LOGGER.info("Successfully connected to %s:%d", self.host, self.port)
                return True

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
        if not self._connected or not self._writer or not self._reader:
            if not await self.connect():
                raise ConnectionError("Not connected to device and reconnection failed")

        try:
            # Send the command
            full_command = f"{command}\r\n"
            await self._send_data(full_command)

            # Read the response
            response = await asyncio.wait_for(
                self._read_until_pattern(b"#"), timeout=self.timeout
            )

            # Clean up the response
            cleaned_response = self._clean_response(response, command)
            return cleaned_response

        except Exception as err:
            _LOGGER.error("Error sending command '%s': %s", command, err)
            await self._cleanup_connection()
            raise

    async def _send_data(self, data: str) -> None:
        """Send data to the device."""
        if self._writer:
            self._writer.write(data.encode())
            await self._writer.drain()

    async def _read_until_pattern(self, pattern: bytes) -> str:
        """Read from the socket until a pattern is matched."""
        buffer = b""

        while True:
            chunk = await self._reader.read(1024)
            if not chunk:
                break

            buffer += chunk
            if pattern in buffer:
                break

        return buffer.decode("utf-8", errors="ignore")

    def _clean_response(self, response: str, command: str) -> str:
        """Clean the response from the device."""
        # Remove the command from the beginning of the response
        if response.startswith(command):
            response = response[len(command) :]

        # Remove the prompt from the end of the response
        if response.endswith("#"):
            response = response[:-1]

        # Remove any leading/trailing whitespace
        response = response.strip()

        return response
