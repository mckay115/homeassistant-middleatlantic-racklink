"""Socket connection handler for Middle Atlantic RackLink devices."""

import asyncio
import logging
import re
from typing import Optional, Tuple

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
            username: Username for authentication (if required)
            password: Password for authentication (if required)
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
        self._prompt_pattern = re.compile(r"[>#:\]](\s|$)")
        self._login_prompt = re.compile(r"(?:login|username|user)[: ]+", re.IGNORECASE)
        self._password_prompt = re.compile(r"password[: ]+", re.IGNORECASE)

    async def connect(self) -> None:
        """Connect to the device and authenticate if needed."""
        try:
            _LOGGER.debug("Connecting to %s:%d", self.host, self.port)

            # Open the socket connection
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=self.timeout
            )

            # Wait for the initial prompt
            initial_output = await asyncio.wait_for(
                self._read_chunk(), timeout=self.timeout
            )

            _LOGGER.debug("Initial connection output: %s", initial_output)

            # Check if we need to authenticate
            if self._login_prompt.search(initial_output):
                if not self.username:
                    raise ValueError("Username is required but not provided")

                # Send username
                _LOGGER.debug("Sending username: %s", self.username)
                await self._send_data(f"{self.username}\r\n")

                # Wait for password prompt
                login_response = await asyncio.wait_for(
                    self._read_chunk(), timeout=self.timeout
                )

                _LOGGER.debug("Login response: %s", login_response)

                if self._password_prompt.search(
                    login_response
                ) or self._password_prompt.search(initial_output):
                    if not self.password:
                        raise ValueError("Password is required but not provided")

                    # Send password
                    _LOGGER.debug("Sending password")
                    await self._send_data(f"{self.password}\r\n")

                    # Wait for command prompt
                    auth_response = await asyncio.wait_for(
                        self._read_chunk(), timeout=self.timeout
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
            _LOGGER.debug("Successfully connected to %s:%d", self.host, self.port)

        except asyncio.TimeoutError:
            _LOGGER.error("Timeout connecting to %s:%d", self.host, self.port)
            await self.disconnect()
            raise

        except Exception as err:
            _LOGGER.error("Error connecting to %s:%d: %s", self.host, self.port, err)
            await self.disconnect()
            raise

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception as err:
                _LOGGER.debug("Error during disconnect: %s", err)

        self._connected = False
        self._reader = None
        self._writer = None
        _LOGGER.debug("Disconnected from %s:%d", self.host, self.port)

    async def send_command(self, command: str) -> str:
        """Send a command to the device and wait for a response.

        Args:
            command: The command to send

        Returns:
            The response from the device

        Raises:
            ConnectionError: If not connected
            TimeoutError: If the command times out
        """
        if not self._connected or not self._writer or not self._reader:
            raise ConnectionError("Not connected to device")

        try:
            # Check if connection is still valid
            if self._writer.is_closing():
                raise ConnectionError("Connection closed by remote device")

            # Send the command
            _LOGGER.debug("Sending command: %s", command)
            await self._send_data(f"{command}\r\n")

            # Wait for and collect the response
            response = await self._read_until_prompt()

            # Clean up the response
            # Remove the command echo and trailing prompt
            cleaned_response = self._clean_response(response, command)

            _LOGGER.debug("Command response: %s", cleaned_response)
            return cleaned_response

        except asyncio.TimeoutError:
            _LOGGER.error("Timeout sending command: %s", command)
            raise TimeoutError(f"Command timed out: {command}")

        except Exception as err:
            _LOGGER.error("Error sending command '%s': %s", command, err)
            raise

    async def _send_data(self, data: str) -> None:
        """Send data to the device."""
        if not self._writer:
            raise ConnectionError("Not connected to device")

        self._writer.write(data.encode("utf-8", errors="replace"))
        await self._writer.drain()

    async def _read_chunk(self, size: int = 4096) -> str:
        """Read a chunk of data from the device."""
        if not self._reader:
            raise ConnectionError("Not connected to device")

        try:
            chunk = await self._reader.read(size)
            return chunk.decode("utf-8", errors="replace")
        except Exception as err:
            _LOGGER.error("Error reading data: %s", err)
            raise

    async def _read_until_prompt(self) -> str:
        """Read data from the connection until a prompt is detected."""
        response = ""
        timeout_counter = 0
        max_iterations = self.timeout * 2  # 500ms per iteration for timeout seconds

        while timeout_counter < max_iterations:
            try:
                # Read a chunk of data with a short timeout
                chunk = await asyncio.wait_for(self._read_chunk(), timeout=0.5)

                if not chunk:
                    # No more data, might be EOF
                    if self._reader.at_eof():
                        _LOGGER.debug("Connection closed while reading response")
                        break
                    timeout_counter += 1
                    continue

                response += chunk

                # Check if we've reached a command prompt
                if self._prompt_pattern.search(response):
                    break

            except asyncio.TimeoutError:
                # Short timeout reached, increment counter and continue
                timeout_counter += 1
                continue

            except Exception as err:
                _LOGGER.error("Error reading response: %s", err)
                raise

        if timeout_counter >= max_iterations:
            _LOGGER.warning("Timed out waiting for prompt, returning partial response")

        return response

    def _clean_response(self, response: str, command: str) -> str:
        """Clean up the command response by removing echoed command and prompt."""
        # Remove the echoed command
        if response.startswith(command):
            response = response[len(command) :]

        # Remove any remaining command text (in case of line breaks)
        response = re.sub(r"^\s*" + re.escape(command) + r"\s*[\r\n]+", "", response)

        # Remove trailing prompt
        response = re.sub(self._prompt_pattern, "", response.rstrip())

        # Remove any ANSI escape sequences
        response = re.sub(r"\x1b\[[0-9;]*[mK]", "", response)

        # Clean up any extra line breaks
        response = re.sub(r"[\r\n]+", "\n", response)

        return response.strip()
