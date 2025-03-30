"""API for Middle Atlantic Racklink PDU devices."""

import asyncio
import logging
import re
import socket
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple, List

_LOGGER = logging.getLogger(__name__)


class RacklinkApi:
    """API Communication with Middle Atlantic RackLink devices."""

    def __init__(
        self,
        host: str,
        port: int = 6000,
        username: str = None,
        password: str = None,
        socket_timeout: float = 5.0,
        login_timeout: float = 10.0,
        command_timeout: float = 5.0,
    ):
        """Initialize the API client."""
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._socket_timeout = socket_timeout
        self._login_timeout = login_timeout
        self._command_timeout = command_timeout

        self._socket = None
        self._connected = False
        self._buffer = b""
        self._command_lock = asyncio.Lock()
        self._retry_count = 0
        self._max_retries = 3
        self._command_delay = (
            0.25  # Delay between commands to avoid overwhelming device
        )
        self._last_error = None
        self._last_error_time = None

    @property
    def connected(self) -> bool:
        """Return if we are connected to the device."""
        return self._connected and self._socket is not None

    @property
    def last_error(self) -> Optional[str]:
        """Return the last error message."""
        return self._last_error

    @property
    def last_error_time(self) -> Optional[datetime]:
        """Return the time of the last error."""
        return self._last_error_time

    def _handle_error(self, error: str) -> None:
        """Handle an error by logging it and updating state."""
        self._last_error = error
        self._last_error_time = datetime.now(timezone.utc)
        _LOGGER.error("Racklink API error: %s", error)

    async def _create_socket_connection(self) -> Optional[socket.socket]:
        """Create a socket connection to the device."""
        loop = asyncio.get_event_loop()

        _LOGGER.debug("Creating socket connection to %s:%s", self._host, self._port)

        try:
            # Create a standard socket in non-blocking mode
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setblocking(False)

            # Set socket options
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

            # Connect using the event loop with timeout
            await loop.sock_connect(sock, (self._host, self._port))

            _LOGGER.debug(
                "Socket connection established to %s:%s", self._host, self._port
            )
            return sock

        except socket.timeout:
            _LOGGER.error(
                "Socket connection to %s:%s timed out", self._host, self._port
            )
            return None
        except ConnectionRefusedError:
            _LOGGER.error(
                "Connection refused to %s:%s - check if device is online and port is correct",
                self._host,
                self._port,
            )
            return None
        except socket.gaierror:
            _LOGGER.error(
                "Could not resolve hostname %s - check network settings", self._host
            )
            return None
        except OSError as e:
            if e.errno == 113:  # No route to host
                _LOGGER.error(
                    "No route to host %s - check network connectivity", self._host
                )
            else:
                _LOGGER.error(
                    "Socket error connecting to %s:%s: %s",
                    self._host,
                    self._port,
                    str(e),
                )
            return None
        except Exception as e:
            _LOGGER.error(
                "Unexpected error creating socket connection to %s:%s: %s",
                self._host,
                self._port,
                str(e),
            )
            return None

    async def _socket_read(self, timeout: float = 1.0) -> bytes:
        """Read data from the socket with a timeout."""
        if not self._socket:
            _LOGGER.debug("Cannot read from socket: socket not connected")
            return b""

        loop = asyncio.get_event_loop()

        try:
            # Define a callback to read from the socket
            def _socket_read_callback():
                try:
                    return self._socket.recv(4096)
                except (BlockingIOError, socket.timeout):
                    # Would block, so no data available (normal for non-blocking sockets)
                    return None
                except ConnectionResetError:
                    _LOGGER.error("Connection was reset while reading")
                    return b""
                except OSError as e:
                    _LOGGER.error("Socket read error: %s", str(e))
                    return b""

            # Wait for data with timeout
            start_time = time.time()
            data = None

            while (time.time() - start_time) < timeout:
                # Try to read with the callback
                try:
                    data = await loop.run_in_executor(None, _socket_read_callback)
                except Exception as e:
                    _LOGGER.error("Error in socket read: %s", str(e))
                    return b""

                # If we got data, return it
                if data is not None and data != b"":
                    return data

                # If we received an empty byte array, the connection was closed
                if data == b"":
                    _LOGGER.debug("Socket read returned empty data - connection closed")
                    self._connected = False
                    return b""

                # Otherwise sleep briefly and try again
                await asyncio.sleep(0.05)

            # If we get here, we timed out
            return b""

        except Exception as e:
            _LOGGER.error("Unexpected error reading from socket: %s", str(e))
            return b""

    async def _socket_write(self, data: bytes) -> None:
        """Write data to the socket."""
        if not self._socket:
            _LOGGER.debug("Cannot write to socket: socket not connected")
            return

        loop = asyncio.get_event_loop()

        if not data.endswith(b"\r\n"):
            data += b"\r\n"

        try:
            # Define a callback to send data through the socket
            def send_data(sock, data_to_send):
                try:
                    return sock.send(data_to_send)
                except (BlockingIOError, socket.timeout):
                    # Would block, which shouldn't happen for sending small commands
                    return 0
                except ConnectionResetError:
                    _LOGGER.error("Connection was reset while writing")
                    return -1
                except OSError as e:
                    _LOGGER.error("Socket write error: %s", str(e))
                    return -1

            # Send the data
            bytes_sent = await loop.run_in_executor(None, send_data, self._socket, data)

            if bytes_sent < 0:
                _LOGGER.debug("Socket error during write")
                self._connected = False
            elif bytes_sent < len(data):
                _LOGGER.debug(
                    "Socket write incomplete: %d of %d bytes sent",
                    bytes_sent,
                    len(data),
                )
            else:
                _LOGGER.debug("Successfully wrote %d bytes to socket", bytes_sent)

        except Exception as e:
            _LOGGER.error("Unexpected error writing to socket: %s", str(e))
            self._connected = False

    async def _close_socket(self) -> None:
        """Close the socket connection."""
        if self._socket:
            loop = asyncio.get_event_loop()

            try:
                # Define a callback to close the socket
                def close_socket(sock):
                    try:
                        sock.close()
                        return True
                    except Exception as e:
                        _LOGGER.error("Error closing socket: %s", str(e))
                        return False

                await loop.run_in_executor(None, close_socket, self._socket)
                _LOGGER.debug("Socket closed")
            except Exception as e:
                _LOGGER.error("Unexpected error closing socket: %s", str(e))
            finally:
                self._socket = None
                self._connected = False
                self._buffer = b""

    async def connect(self) -> bool:
        """Connect to the device and login."""
        if self.connected:
            _LOGGER.debug("Already connected to %s:%s", self._host, self._port)
            return True

        _LOGGER.debug("Connecting to %s:%s", self._host, self._port)

        # Close any existing socket
        await self._close_socket()

        # Create a new socket connection
        self._socket = await self._create_socket_connection()
        if not self._socket:
            self._handle_error(
                f"Failed to create socket connection to {self._host}:{self._port}"
            )
            return False

        # Set initial connection state
        self._connected = True
        self._buffer = b""

        # Attempt to login
        login_success = await self._login()
        if not login_success:
            self._handle_error(f"Failed to login to {self._host}:{self._port}")
            await self._close_socket()
            return False

        _LOGGER.info("Successfully connected to %s:%s", self._host, self._port)
        return True

    async def _login(self) -> bool:
        """Login to the device."""
        if not self._socket:
            return False

        loop = asyncio.get_event_loop()
        login_success = False

        try:
            # Wait for login prompt
            start_time = time.time()
            login_prompt_received = False
            password_prompt_received = False
            login_complete = False

            # Define a callback to read data from the socket
            def socket_read():
                try:
                    return self._socket.recv(4096)
                except (BlockingIOError, socket.timeout):
                    return None
                except Exception as e:
                    _LOGGER.error("Error reading during login: %s", str(e))
                    return b""

            # Wait for login prompt with timeout
            while (
                time.time() - start_time
            ) < self._login_timeout and not login_complete:
                # Read from socket
                data = await loop.run_in_executor(None, socket_read)

                if data is None:
                    # No data yet
                    await asyncio.sleep(0.1)
                    continue
                elif data == b"":
                    # Connection closed
                    _LOGGER.error("Connection closed during login")
                    return False

                # Add data to buffer
                self._buffer += data
                buffer_str = self._buffer.decode("utf-8", errors="ignore")

                # Check for login prompt
                if not login_prompt_received and "Login:" in buffer_str:
                    login_prompt_received = True
                    _LOGGER.debug("Login prompt received")

                    # Send username
                    def send_cmd(sock, data):
                        try:
                            return sock.send(data)
                        except Exception as e:
                            _LOGGER.error("Error sending username: %s", str(e))
                            return -1

                    cmd = f"{self._username}\r\n".encode("utf-8")
                    await loop.run_in_executor(None, send_cmd, self._socket, cmd)
                    _LOGGER.debug("Sent username: %s", self._username)

                    # Clear buffer after sending username
                    self._buffer = b""

                # Check for password prompt
                elif (
                    login_prompt_received
                    and not password_prompt_received
                    and "Password:" in buffer_str
                ):
                    password_prompt_received = True
                    _LOGGER.debug("Password prompt received")

                    # Define a callback to read data from the socket
                    def read_response():
                        try:
                            return self._socket.recv(4096)
                        except Exception as e:
                            _LOGGER.error("Error reading response: %s", str(e))
                            return b""

                    # Send password
                    def send_cmd(sock, data):
                        try:
                            return sock.send(data)
                        except Exception as e:
                            _LOGGER.error("Error sending password: %s", str(e))
                            return -1

                    cmd = f"{self._password}\r\n".encode("utf-8")
                    await loop.run_in_executor(None, send_cmd, self._socket, cmd)
                    _LOGGER.debug("Sent password")

                    # Clear buffer after sending password
                    self._buffer = b""

                # Check for successful login (prompt after password)
                elif password_prompt_received and (
                    ">" in buffer_str or "#" in buffer_str
                ):
                    login_complete = True
                    login_success = True
                    _LOGGER.debug("Login successful")

                # Check for login failure
                elif (
                    "Login incorrect" in buffer_str
                    or "Access denied" in buffer_str
                    or "Authentication failed" in buffer_str
                ):
                    _LOGGER.error("Login failed: incorrect credentials")
                    login_complete = True
                    login_success = False

                # If no match yet, continue waiting
                else:
                    await asyncio.sleep(0.1)

            if not login_complete:
                _LOGGER.error("Login timed out after %s seconds", self._login_timeout)

            return login_success

        except Exception as e:
            _LOGGER.error("Error during login: %s", str(e))
            return False

    async def disconnect(self) -> bool:
        """Disconnect from the device."""
        if not self.connected:
            _LOGGER.debug("Already disconnected")
            return True

        _LOGGER.debug("Disconnecting from %s:%s", self._host, self._port)

        try:
            # Send logout command if connected
            if self._socket and self._connected:
                await self._socket_write(b"exit\r\n")
                # Wait briefly for command to be sent
                await asyncio.sleep(0.2)

            # Close the socket
            await self._close_socket()
            return True

        except Exception as e:
            _LOGGER.error("Error during disconnect: %s", str(e))
            # Still attempt to close the socket
            await self._close_socket()
            return False

    async def send_command(self, command: str, timeout: int = None) -> str:
        """Send a command to the device and return the response."""
        if not self.connected:
            _LOGGER.warning("Cannot send command: not connected")
            return ""

        # Use default timeout if not specified
        if timeout is None:
            timeout = self._command_timeout

        # Acquire lock to ensure exclusive command access
        async with self._command_lock:
            try:
                _LOGGER.debug("Sending command: %s", command)

                # Clear the buffer before sending command
                self._buffer = b""

                # Send the command
                await self._socket_write(command.encode("utf-8"))

                # Read the response with timeout
                response = await self._socket_read_until(b"([>#])", timeout)
                response_text = response.decode("utf-8", errors="ignore")

                # Process the response:
                # 1. Remove command echo
                response_lines = response_text.splitlines()
                if response_lines and command in response_lines[0]:
                    response_lines = response_lines[1:]

                # 2. Remove prompt at end
                if response_lines and (
                    response_lines[-1].endswith(">") or response_lines[-1].endswith("#")
                ):
                    response_lines = response_lines[:-1]

                # Return cleaned response
                return "\n".join(response_lines).strip()

            except asyncio.TimeoutError:
                _LOGGER.error("Command timed out: %s", command)
                return ""
            except Exception as e:
                _LOGGER.error("Error sending command: %s - %s", command, str(e))
                return ""
            finally:
                # Add delay between commands
                await asyncio.sleep(self._command_delay)

    async def _socket_read_until(
        self, pattern: bytes = None, timeout: float = None
    ) -> bytes:
        """Read from socket until a pattern is matched or timeout occurs."""
        if timeout is None:
            timeout = self._command_timeout

        if not pattern:
            pattern = b"([>#])"  # Default prompt pattern

        # Compile pattern if it's a string
        if isinstance(pattern, bytes):
            pattern_re = re.compile(pattern)
        else:
            pattern_re = pattern

        start_time = time.time()
        self._buffer = b""

        while (time.time() - start_time) < timeout:
            # Check if the pattern is already in buffer
            if pattern_re.search(self._buffer):
                return self._buffer

            # Read more data
            data = await self._socket_read(0.5)  # Short timeout for each read

            if data == b"":
                # Connection closed or error
                if self._buffer:
                    return self._buffer  # Return what we have
                return b""

            # Add to buffer and check again
            self._buffer += data

        # Timeout reached
        _LOGGER.debug("Pattern match timed out, returning what we have")
        return self._buffer
