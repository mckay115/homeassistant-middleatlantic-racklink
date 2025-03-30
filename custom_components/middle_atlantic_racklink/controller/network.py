"""Network-related functionality for the RackLink controller."""

import asyncio
import logging
import re
import socket
import time
from datetime import datetime

_LOGGER = logging.getLogger(__name__)


class NetworkMixin:
    """Network functionality for RackLink devices."""

    async def connect(self) -> bool:
        """Connect to the device."""
        if self._connected:
            return True

        async with self._connect_lock:
            try:
                _LOGGER.info(
                    "Connecting to %s:%s as %s", self._host, self._port, self._username
                )

                # Create socket connection
                self._socket = await self._create_socket_connection()
                if not self._socket:
                    _LOGGER.error("Failed to create socket connection")
                    self._handle_error("Failed to create socket connection")
                    return False

                # Log in to the device
                login_success = await self._login()
                if not login_success:
                    _LOGGER.error("Login failed")
                    self._handle_error("Login failed")
                    await self._close_socket()
                    return False

                # Mark as connected
                self._connected = True
                self._available = True
                _LOGGER.info("Successfully connected to %s:%s", self._host, self._port)

                # Initialize command processor if needed
                self._ensure_command_processor_running()

                # Discover valid commands on this device to help with command mapping
                try:
                    _LOGGER.debug("Initiating command discovery to learn device syntax")
                    await self.discover_valid_commands()
                except Exception as e:
                    _LOGGER.warning("Command discovery failed: %s", e)

                # Load initial data
                try:
                    await self._load_initial_data()
                except Exception as e:
                    _LOGGER.warning("Could not load initial data: %s", e)

                return True

            except Exception as e:
                self._handle_error(f"Error connecting: {e}")
                if self._socket:
                    await self._close_socket()
                return False

    async def _create_socket_connection(self) -> socket.socket:
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
            # First check if socket is still valid
            if self._socket.fileno() == -1:
                _LOGGER.warning("Socket is no longer valid (fileno=-1)")
                self._connected = False
                self._available = False
                return b""

            # Create a Future that will receive the data
            read_future = loop.create_future()

            # Define the read callback to be used with add_reader
            def _socket_read_callback():
                if read_future.done():
                    return  # Avoid calling set_result twice

                try:
                    data = self._socket.recv(4096)
                    if data:
                        _LOGGER.debug("Read %d bytes from socket", len(data))
                        read_future.set_result(data)
                    else:
                        # Empty data means the connection was closed
                        _LOGGER.debug(
                            "Socket closed by remote host (received empty data)"
                        )
                        read_future.set_exception(
                            ConnectionError("Connection closed by remote host")
                        )
                        self._connected = False
                except BlockingIOError:
                    # No data available yet
                    pass
                except ConnectionError as e:
                    _LOGGER.debug("Socket connection error: %s", e)
                    self._connected = False
                    if not read_future.done():
                        read_future.set_exception(e)
                except OSError as e:
                    _LOGGER.debug("Socket OS error: %s", e)
                    self._connected = False
                    if not read_future.done():
                        read_future.set_exception(e)
                except Exception as e:
                    _LOGGER.debug("Socket read error: %s", e)
                    if not read_future.done():
                        read_future.set_exception(e)

            # Try to add the socket reader safely
            try:
                loop.add_reader(self._socket.fileno(), _socket_read_callback)
            except (ValueError, OSError) as e:
                _LOGGER.warning("Could not add socket reader: %s", e)
                self._connected = False
                return b""

            try:
                # Wait for the read to complete or timeout
                return await asyncio.wait_for(read_future, timeout=timeout)
            finally:
                # Always try to remove the reader when done
                try:
                    if self._socket and self._socket.fileno() != -1:
                        loop.remove_reader(self._socket.fileno())
                except (ValueError, OSError):
                    # Socket might be closed already
                    pass

        except asyncio.TimeoutError:
            _LOGGER.debug("Socket read timed out after %s seconds", timeout)
            return b""
        except ConnectionError as e:
            _LOGGER.debug("Connection error during socket read: %s", e)
            self._connected = False
            return b""
        except Exception as e:
            _LOGGER.error("Error reading from socket: %s", e)
            return b""

    async def _socket_write(self, data: bytes) -> None:
        """Write to socket connection in a non-blocking way."""
        if not self._socket:
            _LOGGER.error("Cannot write to socket: connection is None")
            raise ConnectionError("No socket connection available")

        if data is None:
            raise ValueError("Data cannot be None")

        try:
            _LOGGER.debug("Writing %d bytes to socket", len(data))
            # Safety check - don't use a direct reference to self._socket that could become None
            socket_connection = self._socket
            if socket_connection is None:
                raise ConnectionError("Socket connection became None")

            # Send data in a separate thread
            def send_data(sock, data_to_send):
                try:
                    sock.sendall(data_to_send)
                    return True
                except Exception as e:
                    _LOGGER.error("Socket send error: %s", e)
                    return False

            success = await asyncio.to_thread(send_data, socket_connection, data)

            if not success:
                raise ConnectionError("Failed to send data through socket")

        except asyncio.CancelledError:
            _LOGGER.warning("Socket write operation was cancelled")
            raise
        except AttributeError as exc:
            self._connected = False
            self._available = False
            raise ConnectionError(f"Socket connection lost: {exc}") from exc
        except Exception as exc:
            self._connected = False
            self._available = False
            raise ConnectionError(f"Error writing to socket: {exc}") from exc

    async def _close_socket(self) -> None:
        """Close the current socket connection safely."""
        if self._socket is not None:
            _LOGGER.debug("Closing existing socket connection")
            try:
                # Close socket in a thread to avoid blocking
                def close_socket(sock):
                    try:
                        sock.close()
                    except Exception as close_err:
                        _LOGGER.debug("Error closing socket: %s", close_err)

                await asyncio.to_thread(close_socket, self._socket)
            except Exception as e:
                _LOGGER.debug("Error closing socket connection: %s", e)
            finally:
                self._socket = None
                self._connected = False
                self._available = False
                self._receive_buffer = b""  # Clear buffer on disconnect

    async def _socket_read_until(
        self, pattern: bytes = None, timeout: float = None
    ) -> bytes:
        """Read from socket connection until pattern is found."""
        if not self._socket:
            _LOGGER.debug("Cannot read until pattern: socket not connected")
            return b""

        if pattern is None:
            pattern = b"#"  # Change default prompt to # which is what the device uses

        timeout = timeout or self._socket_timeout
        start_time = time.time()

        # Import parser for command prompt detection
        from ..parser import is_command_prompt

        # Support specific RackLink prompt patterns based on the response samples
        patterns = [pattern]
        if pattern == b"#":
            # Add command prompt pattern with bracket: [DeviceName] #
            patterns.extend([rb"]\s+#", b">", b"$", b":", b"RackLink>", b"admin>"])
        elif pattern == b"Username:":
            patterns = [b"Username:", b"login:"]  # Username prompts
        elif pattern == b"Password:":
            patterns = [b"Password:", b"password:"]  # Password prompts

        # Use existing buffer or create a new one
        buffer = self._receive_buffer

        # Add a more sophisticated timeout handling with partial matches
        max_attempts = 20
        attempt_count = 0
        last_buffer_size = len(buffer)
        last_data_time = time.time()

        while time.time() - start_time < timeout:
            # Check if any pattern is already in buffer
            for ptn in patterns:
                if ptn in buffer:
                    # Special check for bracket pattern
                    if ptn == rb"]\s+#":
                        # Look for complete prompt pattern [DeviceName] #
                        prompt_matches = re.findall(rb"\[.+?\]\s+#", buffer)
                        if prompt_matches:
                            # Find last occurrence (most recent prompt)
                            last_prompt = prompt_matches[-1]
                            prompt_index = buffer.rfind(last_prompt) + len(last_prompt)
                            result = buffer[:prompt_index]
                            # Save remaining data for next read
                            self._receive_buffer = buffer[prompt_index:]
                            return result
                    else:
                        pattern_index = buffer.find(ptn) + len(ptn)
                        result = buffer[:pattern_index]
                        # Save remaining data for next read
                        self._receive_buffer = buffer[pattern_index:]
                        return result

            # Check for complete command prompt using regex
            if b"#" in buffer:
                # Convert buffer to string for line-by-line checking
                buffer_str = buffer.decode("utf-8", errors="ignore")
                lines = buffer_str.splitlines()

                # Check if any line is a command prompt
                for i, line in enumerate(lines):
                    if is_command_prompt(line):
                        # Calculate how many bytes to include
                        included_lines = "\n".join(lines[: i + 1])
                        bytes_to_include = len(
                            included_lines.encode("utf-8", errors="ignore")
                        )

                        result = buffer[:bytes_to_include]
                        # Save remaining data for next read
                        self._receive_buffer = buffer[bytes_to_include:]
                        return result

            # Check for data timeout - if we haven't received data in a while
            # but the overall timeout hasn't been reached yet
            data_timeout = time.time() - last_data_time > 2.0

            # Break if we've made too many attempts or hit a data timeout
            if attempt_count >= max_attempts or data_timeout:
                _LOGGER.debug(
                    "Breaking read loop after %d attempts or data timeout %s",
                    attempt_count,
                    data_timeout,
                )
                break

            # Calculate remaining time
            remaining_time = timeout - (time.time() - start_time)
            if remaining_time <= 0:
                break

            # Read more data with shorter timeouts for responsiveness
            read_timeout = min(0.5, remaining_time)

            try:
                data = await self._socket_read(timeout=read_timeout)
                attempt_count += 1

                if not data:
                    # No data received in this read attempt
                    if time.time() - start_time >= timeout:
                        # Overall timeout reached
                        _LOGGER.debug("Timeout reached while waiting for pattern")
                        break

                    # Brief pause before next attempt to avoid CPU spinning
                    await asyncio.sleep(0.1)
                    continue

                # Got new data - update last received time
                last_data_time = time.time()

                # Append new data to buffer
                buffer += data
                _LOGGER.debug(
                    "Read %d bytes from socket, buffer now %d bytes, looking for %s",
                    len(data),
                    len(buffer),
                    pattern,
                )

                # If buffer size hasn't changed significantly after multiple attempts,
                # we might be stuck in a loop without receiving the prompt
                if len(buffer) - last_buffer_size < 2 and attempt_count > 5:
                    _LOGGER.debug("Buffer size not increasing, may be missing prompt")
                    # Look for potential command prompts
                    if b"#" in buffer or b">" in buffer:
                        _LOGGER.debug("Found potential prompt character in buffer")
                        # Just before timeout, check if buffer looks complete
                        buffer_str = buffer.decode("utf-8", errors="ignore")
                        if re.search(r"\[[^\]]+\]\s+#", buffer_str):
                            _LOGGER.debug(
                                "Found command prompt pattern, returning data"
                            )
                            # Return the buffer and let the caller parse it
                            result = buffer
                            self._receive_buffer = b""
                            return result

                    # Force return what we have if content seems substantial
                    if len(buffer) > 20:
                        break

                last_buffer_size = len(buffer)

                # Debug output for raw data to help debug connection issues
                if len(buffer) < 200:  # Only log small buffers to avoid flooding
                    _LOGGER.debug("Buffer content: %s", buffer)

            except Exception as e:
                _LOGGER.error("Error reading until pattern: %s", e)
                self._connected = False
                return b""

        # Timeout reached, log and return partial data
        _LOGGER.debug(
            "Read until timeout (%s seconds) - returning %d bytes of partial data",
            timeout,
            len(buffer),
        )

        # Save the buffer for future reads
        self._receive_buffer = buffer
        return buffer

    async def _login(self) -> bool:
        """Login to the device if needed."""
        try:
            if not self._socket:
                _LOGGER.error("Cannot login: no socket connection")
                return False

            _LOGGER.debug("Starting login sequence for %s", self._host)

            # Step 1: Wait for username prompt
            _LOGGER.debug("Waiting for username prompt...")
            try:
                username_response = await self._socket_read_until(
                    b"Username:", timeout=self._login_timeout
                )
                if not username_response:
                    _LOGGER.error("Username prompt not detected")
                    return False
                _LOGGER.debug("Username prompt detected")
            except Exception as e:
                _LOGGER.error("Error waiting for username prompt: %s", e)
                return False

            # Step 2: Send username
            _LOGGER.debug("Sending username: %s", self._username)
            try:
                await self._socket_write(f"{self._username}\r\n".encode())
                # Give device time to process
                await asyncio.sleep(0.5)
            except Exception as e:
                _LOGGER.error("Error sending username: %s", e)
                return False

            # Step 3: Wait for password prompt
            _LOGGER.debug("Waiting for password prompt...")
            try:
                password_response = await self._socket_read_until(
                    b"Password:", timeout=self._login_timeout
                )
                if not password_response:
                    _LOGGER.error("Password prompt not detected")
                    return False
                _LOGGER.debug("Password prompt detected")
            except Exception as e:
                _LOGGER.error("Error waiting for password prompt: %s", e)
                return False

            # Step 4: Send password
            _LOGGER.debug("Sending password...")
            try:
                await self._socket_write(f"{self._password}\r\n".encode())
                # Give device time to process
                await asyncio.sleep(1)
            except Exception as e:
                _LOGGER.error("Error sending password: %s", e)
                return False

            # Step 5: Wait for command prompt
            _LOGGER.debug("Waiting for command prompt...")
            try:
                # Look specifically for the # prompt (most common for this device)
                command_response = await self._socket_read_until(
                    b"#", timeout=self._login_timeout
                )
                if not command_response:
                    _LOGGER.error("Command prompt not detected")
                    return False
                _LOGGER.debug("Command prompt detected - login successful")
            except Exception as e:
                _LOGGER.error("Error waiting for command prompt: %s", e)
                return False

            _LOGGER.info("Successfully logged in to %s", self._host)
            return True

        except Exception as e:
            _LOGGER.error("Unexpected error during login: %s", e)
            return False

    async def disconnect(self) -> bool:
        """Disconnect from the PDU - used by config_flow for validation."""
        try:
            await self._close_socket()
            return True
        except Exception as e:
            _LOGGER.error("Error disconnecting: %s", e)
            return False

    async def shutdown(self) -> None:
        """Gracefully shut down the controller and release resources."""
        _LOGGER.debug("Shutting down controller for %s", self._host)

        # Flag that we're shutting down so other tasks can exit cleanly
        self._shutdown_requested = True

        # Cancel the command processor task
        if self._command_processor_task and not self._command_processor_task.done():
            _LOGGER.debug("Cancelling command processor task")
            self._command_processor_task.cancel()
            try:
                # Wait briefly for cancellation
                await asyncio.wait_for(
                    asyncio.shield(self._command_processor_task), timeout=1
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        # Cancel any connection task
        if self._connection_task and not self._connection_task.done():
            _LOGGER.debug("Cancelling connection task")
            self._connection_task.cancel()
            try:
                # Wait briefly for cancellation
                await asyncio.wait_for(asyncio.shield(self._connection_task), timeout=1)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        # Close socket connection
        await self._close_socket()

        _LOGGER.info("Controller for %s has been shut down", self._host)

    async def start_background_connection(self) -> None:
        """Start background connection task that won't block Home Assistant startup."""
        if self._connection_task is not None and not self._connection_task.done():
            _LOGGER.debug("Background connection task already running")
            return

        _LOGGER.debug("Starting background connection for %s", self._host)
        self._connection_task = asyncio.create_task(self._background_connect())

    async def _background_connect(self) -> bool:
        """Try to connect in the background."""
        if self._connected and self._socket:
            return True

        _LOGGER.debug(
            "Starting background connection attempt to %s:%s", self._host, self._port
        )

        # First try the regular connection method
        if await self.connect():
            _LOGGER.info("Background connection successful")
            return True

        # If standard connection failed, try the reconnect method which attempts alternative ports
        if await self.reconnect():
            _LOGGER.info(
                "Background connection successful using alternative connection method"
            )
            return True

        # If both methods failed, schedule a future reconnect attempt
        _LOGGER.warning("Background connection failed, scheduling delayed reconnect")
        self._schedule_reconnect()
        return False

    def _schedule_reconnect(self):
        """Schedule a reconnection with exponential backoff."""
        if not hasattr(self, "_retry_count"):
            self._retry_count = 0

        self._retry_count += 1

        # Calculate backoff delay with max of 5 minutes (300 seconds)
        delay = min(300, 2 ** min(self._retry_count, 8))

        _LOGGER.info("Will attempt to reconnect to %s in %s seconds", self._host, delay)

        # Schedule reconnection
        asyncio.create_task(self._delayed_reconnect(delay))

    async def _delayed_reconnect(self, delay):
        """Reconnect after a delay."""
        _LOGGER.debug("Scheduled reconnection to %s in %s seconds", self._host, delay)
        try:
            await asyncio.sleep(delay)

            # Only reconnect if we're still not connected
            if not self._connected:
                _LOGGER.debug("Attempting scheduled reconnection to %s", self._host)

                # Try to connect with a reasonable timeout
                try:
                    await asyncio.wait_for(
                        self._background_connect(), timeout=self._login_timeout
                    )
                except asyncio.TimeoutError:
                    _LOGGER.error("Scheduled reconnection to %s timed out", self._host)
                    self._schedule_reconnect()  # Try again with increased backoff
                except Exception as e:
                    _LOGGER.error(
                        "Error in scheduled reconnection to %s: %s", self._host, e
                    )
                    self._schedule_reconnect()  # Try again with increased backoff
            else:
                _LOGGER.debug("Already connected, skipping scheduled reconnection")
        except asyncio.CancelledError:
            _LOGGER.debug("Delayed reconnection task was cancelled")
        except Exception as e:
            _LOGGER.error("Error in delayed reconnection: %s", e)
            self._schedule_reconnect()  # Try again with increased backoff

    async def reconnect(self) -> bool:
        """Reconnect to the device if not already connected."""
        async with self._connect_lock:
            if self._connected and self._socket:
                _LOGGER.debug("Already connected, no need to reconnect")
                return True

            _LOGGER.info("Attempting to reconnect to %s:%s", self._host, self._port)

            # Close any existing socket first
            await self._close_socket()

            # Save the original port for reference
            original_port = self._port

            # Try different port numbers if the default doesn't work
            potential_ports = [original_port]

            # If the port is the default (6000), also try some alternative common ports
            if original_port == 6000:
                potential_ports.extend([23, 22, 2000, 4000, 8000, 6001])

            # Try each port in sequence
            for port in potential_ports:
                if port != original_port:
                    _LOGGER.info("Trying alternative port %s for %s", port, self._host)

                self._port = port  # Set the current port we're trying

                try:
                    # Create socket with a reasonable timeout
                    self._socket = await asyncio.wait_for(
                        self._create_socket_connection(), timeout=5
                    )

                    if not self._socket:
                        _LOGGER.debug(
                            "Failed to reconnect on port %s - socket creation failed",
                            port,
                        )
                        continue  # Try next port

                    # Try to log in
                    login_success = await asyncio.wait_for(self._login(), timeout=5)

                    if login_success:
                        self._connected = True
                        self._available = True

                        # If this was an alternative port, log that we found a working port
                        if port != original_port:
                            _LOGGER.warning(
                                "Successfully connected to %s using alternative port %s instead of %s",
                                self._host,
                                port,
                                original_port,
                            )
                        else:
                            _LOGGER.info(
                                "Successfully reconnected to %s on port %s",
                                self._host,
                                port,
                            )

                        # Ensure command processor is running
                        self._ensure_command_processor_running()

                        # Update device info
                        await self.get_device_info()

                        return True
                    else:
                        _LOGGER.debug("Failed to log in on port %s", port)
                        await self._close_socket()

                except asyncio.TimeoutError:
                    _LOGGER.debug(
                        "Connection attempt to %s on port %s timed out",
                        self._host,
                        port,
                    )
                    await self._close_socket()
                except Exception as e:
                    _LOGGER.debug(
                        "Error during reconnection to %s on port %s: %s",
                        self._host,
                        port,
                        e,
                    )
                    await self._close_socket()

            # If we get here, we failed to connect on any port
            _LOGGER.error("Failed to reconnect to %s on any port", self._host)

            # Restore original port
            self._port = original_port
            return False

    async def _handle_connection_issues(self) -> bool:
        """Handle connection issues by attempting to reconnect.

        Returns True if successfully reconnected.
        """
        if not self._connected:
            _LOGGER.debug("Connection issue detected, attempting to reconnect")

            # Try immediate reconnect (this will try multiple ports)
            reconnect_success = await self.reconnect()

            if reconnect_success:
                _LOGGER.info("Successfully reconnected")
                return True
            else:
                _LOGGER.error("Failed to reconnect after multiple attempts")
                # Schedule a delayed reconnect with exponential backoff
                self._schedule_reconnect()
                return False
        return True  # Already connected
