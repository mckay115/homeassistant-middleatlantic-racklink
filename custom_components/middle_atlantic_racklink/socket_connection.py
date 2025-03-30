"""Socket connection handler for Middle Atlantic RackLink devices."""

import asyncio
import logging
import re
import time
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
        retry_delay: int = 5,
        max_retries: int = 3,
        keepalive_interval: int = 60,
    ) -> None:
        """Initialize the socket connection.

        Args:
            host: Hostname or IP address of the device
            port: Port number for connection
            username: Username for authentication (if required)
            password: Password for authentication (if required)
            timeout: Timeout in seconds for connections and commands
            retry_delay: Initial delay between connection retries (seconds)
            max_retries: Maximum number of connection retry attempts
            keepalive_interval: Interval for sending keepalive commands (seconds)
        """
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout or 20  # Ensure timeout is not None
        self.retry_delay = retry_delay or 5  # Ensure retry_delay is not None
        self.max_retries = max_retries or 3  # Ensure max_retries is not None
        self.keepalive_interval = (
            keepalive_interval or 60
        )  # Ensure keepalive_interval is not None
        self._reader = None
        self._writer = None
        self._connected = False
        self._prompt_pattern = re.compile(r"[>#:\]](\s|$)")
        self._login_prompt = re.compile(r"(?:login|username|user)[: ]+", re.IGNORECASE)
        self._password_prompt = re.compile(r"password[: ]+", re.IGNORECASE)
        self._last_activity = time.time()  # Initialize with current time
        self._keepalive_task = None
        self._connection_lock = asyncio.Lock()

    async def connect(self) -> bool:
        """Connect to the device and authenticate if needed."""
        async with self._connection_lock:
            if self._connected and self._writer and not self._writer.is_closing():
                return True

            retry_count = 0
            retry_delay = self.retry_delay

            while retry_count < self.max_retries:
                try:
                    _LOGGER.debug(
                        "Connecting to %s:%d (attempt %d/%d)",
                        self.host,
                        self.port,
                        retry_count + 1,
                        self.max_retries,
                    )

                    # Open the socket connection
                    self._reader, self._writer = await asyncio.wait_for(
                        asyncio.open_connection(self.host, self.port),
                        timeout=self.timeout,
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
                                raise ValueError(
                                    "Password is required but not provided"
                                )

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
                    self._last_activity = time.time()

                    # Start keepalive task
                    self._start_keepalive()

                    _LOGGER.debug(
                        "Successfully connected to %s:%d", self.host, self.port
                    )
                    return True

                except asyncio.TimeoutError:
                    _LOGGER.warning(
                        "Timeout connecting to %s:%d (attempt %d/%d)",
                        self.host,
                        self.port,
                        retry_count + 1,
                        self.max_retries,
                    )
                    # Clean up before retry
                    await self._cleanup_connection()

                except Exception as err:
                    _LOGGER.error(
                        "Error connecting to %s:%d: %s (attempt %d/%d)",
                        self.host,
                        self.port,
                        err,
                        retry_count + 1,
                        self.max_retries,
                    )
                    # Clean up before retry
                    await self._cleanup_connection()

                # Increment retry count and delay
                retry_count += 1
                if retry_count < self.max_retries:
                    try:
                        # Safer calculation with explicit float conversion and error handling
                        current_time = asyncio.get_event_loop().time()
                        random_factor = 0.5 - (current_time % 1)
                        jitter = float(retry_delay) * 0.2 * float(random_factor)
                        delay = float(retry_delay) + jitter
                        _LOGGER.debug("Retrying connection in %.1f seconds", delay)
                        await asyncio.sleep(delay)
                        # Increase delay for next retry (exponential backoff)
                        retry_delay = min(retry_delay * 1.5, 30)  # Cap at 30 seconds
                    except (TypeError, ValueError) as err:
                        _LOGGER.warning(
                            "Error calculating retry delay: %s, using default", err
                        )
                        await asyncio.sleep(5)  # Use a safe default

            _LOGGER.error(
                "Failed to connect to %s:%d after %d attempts",
                self.host,
                self.port,
                self.max_retries,
            )
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
            # Stop keepalive
            self._stop_keepalive()

            await self._cleanup_connection()
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
        max_retries = 2  # Maximum retries for a single command
        retry_count = 0

        while retry_count < max_retries:
            if not self._connected or not self._writer or not self._reader:
                # Try to reconnect
                if not await self.connect():
                    raise ConnectionError(
                        "Not connected to device and reconnection failed"
                    )

            try:
                # Check if connection is still valid
                if self._writer.is_closing():
                    # Try to reconnect
                    if not await self.connect():
                        raise ConnectionError(
                            "Connection closed by remote device and reconnection failed"
                        )

                # Send the command
                _LOGGER.debug("Sending command: %s", command)
                await self._send_data(f"{command}\r\n")

                # Update last activity time
                self._last_activity = time.time()

                # Wait for and collect the response
                response = await self._read_until_prompt()

                # Clean up the response
                # Remove the command echo and trailing prompt
                cleaned_response = self._clean_response(response, command)

                _LOGGER.debug("Command response: %s", cleaned_response)
                return cleaned_response

            except asyncio.TimeoutError:
                _LOGGER.warning(
                    "Timeout sending command: %s (attempt %d/%d)",
                    command,
                    retry_count + 1,
                    max_retries,
                )
                retry_count += 1

                # Try to clean up and reconnect
                await self._cleanup_connection()
                if retry_count < max_retries:
                    await asyncio.sleep(2)  # Wait before retry
                    continue
                else:
                    _LOGGER.error(
                        "Command timed out after %d attempts: %s", max_retries, command
                    )
                    raise TimeoutError(f"Command timed out: {command}")

            except (ConnectionError, OSError) as err:
                _LOGGER.warning(
                    "Connection error sending command '%s': %s (attempt %d/%d)",
                    command,
                    err,
                    retry_count + 1,
                    max_retries,
                )
                retry_count += 1

                # Try to clean up and reconnect
                await self._cleanup_connection()
                if retry_count < max_retries:
                    await asyncio.sleep(2)  # Wait before retry
                    continue
                else:
                    raise

            except Exception as err:
                _LOGGER.error("Error sending command '%s': %s", command, err)
                raise

    def _start_keepalive(self) -> None:
        """Start the keepalive task if needed."""
        if not self.keepalive_interval or self.keepalive_interval <= 0:
            return

        if self._keepalive_task and not self._keepalive_task.done():
            return

        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    def _stop_keepalive(self) -> None:
        """Stop the keepalive task."""
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
            self._keepalive_task = None

    async def _keepalive_loop(self) -> None:
        """Send periodic keepalive commands to maintain the connection."""
        try:
            while self._connected:
                await asyncio.sleep(5)  # Check every 5 seconds

                # If there's been recent activity, don't send keepalive yet
                current_time = time.time()
                elapsed = current_time - self._last_activity

                if elapsed < self.keepalive_interval:
                    continue

                # Send a safe command that won't change device state
                try:
                    _LOGGER.debug("Sending keepalive command")
                    await self._send_data("\r\n")  # Just send a newline as keepalive
                    # Update last activity time
                    self._last_activity = time.time()
                except Exception as err:
                    _LOGGER.warning("Keepalive command failed: %s", err)
                    # The main send_command method will handle reconnection if needed
        except asyncio.CancelledError:
            _LOGGER.debug("Keepalive task cancelled")
        except Exception as err:
            _LOGGER.error("Error in keepalive loop: %s", err)

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
            # If the chunk is empty and we're at EOF, the connection is closed
            if not chunk and self._reader.at_eof():
                raise ConnectionError("Connection closed by remote")

            return chunk.decode("utf-8", errors="replace")

        except ConnectionError:
            # Re-raise connection errors directly
            raise

        except UnicodeDecodeError as err:
            # Handle decode errors gracefully
            _LOGGER.warning(
                "Unicode decode error: %s, using replacement character", err
            )
            return chunk.decode("utf-8", errors="replace")

        except (OSError, asyncio.IncompleteReadError) as err:
            # Convert socket/stream errors to ConnectionError
            _LOGGER.error("Socket/stream error reading data: %s", err)
            raise ConnectionError(f"Connection error: {err}")

        except Exception as err:
            _LOGGER.error("Error reading data: %s", err)
            raise

    async def _read_until_prompt(self) -> str:
        """Read data from the connection until a prompt is detected."""
        response = ""
        timeout_counter = 0
        max_iterations = self.timeout * 2  # 500ms per iteration for timeout seconds

        # Pattern that might indicate the end of output even without a prompt
        end_patterns = [
            r"\r\n\r\n$",  # Double blank line
            r"Press any key to continue",
            r"--More--",
            r"Press ENTER to continue",
        ]
        end_regex = re.compile("|".join(end_patterns))

        while timeout_counter < max_iterations:
            try:
                # Read a chunk of data with a short timeout
                chunk = await asyncio.wait_for(self._read_chunk(), timeout=0.5)

                if not chunk:
                    # No more data, might be EOF
                    if self._reader and self._reader.at_eof():
                        _LOGGER.debug("Connection closed while reading response")
                        break
                    timeout_counter += 1

                    # After 3 consecutive empty reads, assume we're waiting for nothing
                    if timeout_counter >= 3:
                        _LOGGER.debug(
                            "Multiple empty reads, assuming response complete"
                        )
                        break

                    continue

                # Reset timeout counter when we get data
                timeout_counter = 0
                response += chunk

                # Check if we've reached a command prompt
                if self._prompt_pattern.search(response):
                    _LOGGER.debug("Found prompt, ending read")
                    break

                # Check for other patterns that might indicate end of output
                if end_regex.search(response):
                    _LOGGER.debug("Found end pattern, ending read")
                    break

            except asyncio.TimeoutError:
                # Short timeout reached, increment counter and continue
                timeout_counter += 1
                # More detailed logging with counter
                if timeout_counter % 4 == 0:  # Log every 2 seconds (4 * 0.5s timeout)
                    _LOGGER.debug(
                        "Waiting for response... (%d/%d)",
                        timeout_counter,
                        max_iterations,
                    )
                continue

            except ConnectionError as err:
                _LOGGER.error("Connection error while reading response: %s", err)
                raise

            except Exception as err:
                _LOGGER.error("Error reading response: %s", err)
                raise

        if timeout_counter >= max_iterations:
            _LOGGER.warning("Timed out waiting for prompt, returning partial response")
            # Log partial response for debugging
            _LOGGER.debug(
                "Partial response (first 100 chars): %s",
                response[:100].replace("\n", "\\n").replace("\r", "\\r"),
            )

        return response

    def _clean_response(self, response: str, command: str) -> str:
        """Clean up the command response by removing echoed command and prompt."""
        if not response:
            return ""

        # Remove the echoed command
        if response.startswith(command):
            response = response[len(command) :]

        # Remove any remaining command text (in case of line breaks)
        try:
            response = re.sub(
                r"^\s*" + re.escape(command) + r"\s*[\r\n]+", "", response
            )
        except Exception as e:
            _LOGGER.debug("Error removing command from response: %s", e)

        # Remove trailing prompt
        try:
            response = re.sub(self._prompt_pattern, "", response.rstrip())
        except Exception as e:
            _LOGGER.debug("Error removing prompt from response: %s", e)

        # Remove any ANSI escape sequences
        try:
            response = re.sub(r"\x1b\[[0-9;]*[mK]", "", response)
        except Exception as e:
            _LOGGER.debug("Error removing ANSI sequences from response: %s", e)

        # Clean up any extra line breaks
        try:
            response = re.sub(r"[\r\n]+", "\n", response)
        except Exception as e:
            _LOGGER.debug("Error cleaning line breaks in response: %s", e)

        return response.strip()
