"""Socket connection handler for Middle Atlantic RackLink devices."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional, Tuple

_LOGGER = logging.getLogger(__name__)


@dataclass
class SocketConfig:
    """Configuration for socket connection."""

    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    timeout: int = 20


class SocketConnection:
    """Socket connection manager for Middle Atlantic RackLink devices."""

    def __init__(self, config: SocketConfig) -> None:
        """Initialize the socket connection.

        Args:
            config: Configuration object containing connection details
        """
        self.config = config
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False
        self._connection_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()  # Lock to prevent concurrent commands

    async def _handle_authentication(self) -> bool:
        """Handle the authentication process.

        Returns:
            bool: True if authentication was successful, False otherwise
        """
        if not (self.config.username and self.config.password):
            return True

        try:
            # Send username
            _LOGGER.debug("Sending username: %s", self.config.username)
            await self._send_data(f"{self.config.username}\r\n")
            await asyncio.sleep(0.5)

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
            await self._send_data(f"{self.config.password}\r\n")
            await asyncio.sleep(0.5)

            # Wait for command prompt
            auth_response = await asyncio.wait_for(self._reader.read(1024), timeout=5.0)
            _LOGGER.debug(
                "Authentication response: %r",
                auth_response.decode("utf-8", errors="ignore"),
            )

            # Check for authentication errors
            decoded_response = auth_response.decode("utf-8", errors="ignore").lower()
            if "invalid" in decoded_response or "failed" in decoded_response:
                _LOGGER.error("Authentication failed: %r", auth_response)
                raise ValueError("Authentication failed")

            return True

        except (asyncio.TimeoutError, ValueError) as err:
            _LOGGER.error("Authentication error: %s", err)
            return False

    async def _establish_connection(self) -> bool:
        """Establish the initial connection.

        Returns:
            bool: True if connection was successful, False otherwise
        """
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.config.host, self.config.port),
                timeout=self.config.timeout,
            )

            # Wait for the initial prompt
            initial_data = await asyncio.wait_for(self._reader.read(1024), timeout=5.0)
            _LOGGER.debug(
                "Initial connection output: %r",
                initial_data.decode("utf-8", errors="ignore"),
            )

            await asyncio.sleep(1)
            return True

        except (asyncio.TimeoutError, ConnectionError, OSError) as err:
            _LOGGER.error("Connection error: %s", err)
            return False

    async def connect(self) -> bool:
        """Connect to the device and authenticate if needed."""
        async with self._connection_lock:
            if self._connected and self._writer and not self._writer.is_closing():
                _LOGGER.debug("Already connected")
                return True

            try:
                _LOGGER.info("Connecting to %s:%d", self.config.host, self.config.port)

                if not await self._establish_connection():
                    return False

                if not await self._handle_authentication():
                    await self._cleanup_connection()
                    return False

                # Clear buffer and get to a clean prompt
                await self._clear_buffer()
                await self._send_data("\r\n")
                await asyncio.sleep(0.5)
                await self._clear_buffer()

                self._connected = True
                _LOGGER.info(
                    "Successfully connected to %s:%d",
                    self.config.host,
                    self.config.port,
                )
                return True

            except (asyncio.TimeoutError, ConnectionError, OSError) as err:
                _LOGGER.error(
                    "Error connecting to %s:%d: %s",
                    self.config.host,
                    self.config.port,
                    err,
                )
                await self._cleanup_connection()
                return False

    async def _cleanup_connection(self) -> None:
        """Clean up the connection resources."""
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except (ConnectionError, OSError) as err:
                _LOGGER.debug("Error during connection cleanup: %s", err)

        self._reader = None
        self._writer = None
        self._connected = False

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        async with self._connection_lock:
            await self._cleanup_connection()
            _LOGGER.debug("Disconnected from %s:%d", self.config.host, self.config.port)

    async def _read_response(self, timeout: float) -> Tuple[str, bool]:
        """Read response from the device with timeout.

        Args:
            timeout: Timeout in seconds

        Returns:
            Tuple[str, bool]: Response string and whether prompt was found
        """
        response = ""
        prompt_found = False
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                chunk = await asyncio.wait_for(self._reader.read(1024), timeout=1.0)
                if not chunk:
                    break

                chunk_str = chunk.decode("utf-8", errors="ignore")
                response += chunk_str

                if "error" in chunk_str.lower():
                    _LOGGER.warning("Error detected in response: %r", chunk_str)
                    continue

                if ">" in chunk_str:
                    prompt_found = True
                    break

            except asyncio.TimeoutError:
                continue
            except (ConnectionError, OSError) as err:
                _LOGGER.error("Connection error during read: %s", err)
                raise

        return response, prompt_found

    async def send_command(self, command: str) -> str:
        """Send a command to the device and wait for a response."""
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
                    if self._writer.is_closing():
                        _LOGGER.debug("Writer is closing, attempting to reconnect")
                        await self._cleanup_connection()
                        if not await self.connect():
                            raise ConnectionError(
                                "Connection closed and reconnection failed"
                            )

                    await self._clear_buffer()
                    _LOGGER.debug("RAW COMMAND >>: %r", command)

                    cmd_line = command.strip()
                    full_command = f"{cmd_line}\r\n"
                    await self._send_data(full_command)
                    await asyncio.sleep(0.1)

                    response, _ = await self._read_response(self.config.timeout)
                    cleaned_response = self._clean_response(response, command)
                    _LOGGER.debug("RAW RESPONSE <<: %r", cleaned_response)
                    return cleaned_response

                except (ConnectionError, OSError) as err:
                    _LOGGER.error("Connection error during command: %s", err)
                    await self._cleanup_connection()
                    retry_count += 1
                    if retry_count < max_retries:
                        _LOGGER.info("Retrying command after error")
                        await asyncio.sleep(1)
                        continue
                    raise ConnectionError(f"Error sending command: {err}") from err

            raise TimeoutError(f"Command timed out after {max_retries} retries")

    async def _send_data(self, data: str) -> None:
        """Send data to the device."""
        if not self._writer:
            raise ConnectionError("Not connected to device")

        try:
            self._writer.write(data.encode("utf-8"))
            await self._writer.drain()
        except (ConnectionError, OSError) as err:
            _LOGGER.error("Error sending data: %s", err)
            raise ConnectionError(f"Error sending data: {err}") from err

    async def _clear_buffer(self) -> None:
        """Clear any pending data in the read buffer."""
        if not self._reader:
            return

        try:
            while True:
                # Check if there's data available to read
                data = await asyncio.wait_for(self._reader.read(1024), timeout=0.1)
                if not data:
                    break
        except asyncio.TimeoutError:
            # No more data to read
            pass
        except (ConnectionError, OSError) as err:
            _LOGGER.debug("Error clearing buffer: %s", err)

    def _clean_response(self, response: str, command: str) -> str:
        """Clean the response string.

        Args:
            response: Raw response from the device
            command: Original command that was sent

        Returns:
            Cleaned response string
        """
        # Split into lines and remove empty ones
        lines = [line.strip() for line in response.splitlines() if line.strip()]

        # Remove the command echo if present
        if lines and lines[0].strip() == command.strip():
            lines.pop(0)

        # Remove any prompt characters
        lines = [line for line in lines if not line.strip() == ">"]

        # Join remaining lines
        return "\n".join(lines).strip()
