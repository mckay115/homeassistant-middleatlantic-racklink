"""Socket connection handler for Middle Atlantic RackLink devices using binary protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, List
import struct

import asyncio
import logging
import time

_LOGGER = logging.getLogger(__name__)

# Protocol constants
HEADER_BYTE = 0xFE
TAIL_BYTE = 0xFF
ESCAPE_BYTE = 0xFD

# Commands
CMD_PING = 0x01
CMD_LOGIN = 0x02
CMD_OUTLET = 0x20
CMD_NACK = 0x10

# Subcommands
SUBCMD_PING = 0x01
SUBCMD_PONG = 0x10
SUBCMD_LOGIN = 0x01
SUBCMD_OUTLET_SET = 0x01
SUBCMD_OUTLET_GET = 0x02

# Outlet states
OUTLET_OFF = 0x00
OUTLET_ON = 0x01
OUTLET_CYCLE = 0x02

# NACK error codes
NACK_BAD_CRC = 0x01
NACK_INVALID_CREDENTIALS = 0x08
NACK_ACCESS_DENIED = 0x11


@dataclass
class SocketConfig:
    """Configuration for socket connection."""

    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    timeout: int = 20


class RackLinkMessage:
    """Represents a RackLink protocol message."""

    def __init__(self, command: int, subcommand: int, data: bytes = b""):
        self.command = command
        self.subcommand = subcommand
        self.data = data

    def build(self) -> bytes:
        """Build the complete message with header, length, checksum, and tail."""
        # Build data envelope: command + subcommand + data
        data_envelope = bytes([self.command, self.subcommand]) + self.data

        # Calculate length
        length = len(data_envelope)

        # Build message without escaping first
        message = bytes([HEADER_BYTE, length]) + data_envelope

        # Calculate checksum
        checksum = self._calculate_checksum(message) & 0x7F

        # Add checksum and tail
        message = message + bytes([checksum, TAIL_BYTE])

        # Apply escape characters
        escaped_message = self._escape_message(message)

        return escaped_message

    def _calculate_checksum(self, data: bytes) -> int:
        """Calculate checksum as sum of all bytes masked with 0x7F."""
        return sum(data) & 0x7F

    def _escape_message(self, message: bytes) -> bytes:
        """Apply escape characters to message body (excluding header and tail)."""
        result = bytearray()

        for i, byte in enumerate(message):
            if i == 0:  # Header - don't escape
                result.append(byte)
            elif i == len(message) - 1:  # Tail - don't escape
                result.append(byte)
            else:  # Message body - apply escaping
                if byte in [HEADER_BYTE, TAIL_BYTE, ESCAPE_BYTE]:
                    result.append(ESCAPE_BYTE)
                    result.append(byte ^ 0xFF)  # Invert bits
                else:
                    result.append(byte)

        return bytes(result)


class SocketConnection:
    """Socket connection manager for Middle Atlantic RackLink devices using binary protocol."""

    def __init__(self, config: SocketConfig) -> None:
        """Initialize the socket connection.

        Args:
            config: Configuration object containing connection details
        """
        self.config = config
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False
        self._authenticated = False
        self._connection_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()
        self._ping_task: Optional[asyncio.Task] = None
        self._last_ping_time = 0.0

    async def _handle_authentication(self) -> bool:
        """Handle RackLink binary protocol authentication.

        Sends login command with username|password format.

        Returns:
            bool: True if authentication was successful, False otherwise
        """
        if not (self.config.username and self.config.password):
            _LOGGER.error("Username and password required for RackLink authentication")
            return False

        try:
            # Create login message: username|password
            credentials = f"{self.config.username}|{self.config.password}"
            login_data = credentials.encode("ascii")

            # Build login message
            login_msg = RackLinkMessage(CMD_LOGIN, SUBCMD_LOGIN, login_data)
            message_bytes = login_msg.build()

            _LOGGER.debug("Sending login message: %s", message_bytes.hex())
            await self._send_raw_data(message_bytes)

            # Wait for response
            response = await self._read_message()
            if not response:
                _LOGGER.error("No response to login command")
                return False

            # Check for NACK (error response)
            if len(response) >= 2 and response[0] == CMD_NACK:
                error_code = response[1] if len(response) > 1 else 0x00
                if error_code == NACK_INVALID_CREDENTIALS:
                    _LOGGER.error("Authentication failed: Invalid credentials")
                elif error_code == NACK_ACCESS_DENIED:
                    _LOGGER.error("Authentication failed: Access denied")
                else:
                    _LOGGER.error(
                        "Authentication failed: NACK error code 0x%02X", error_code
                    )
                return False

            # Successful authentication
            _LOGGER.info("RackLink authentication successful")
            self._authenticated = True

            # Start ping handler to maintain connection
            self._ping_task = asyncio.create_task(self._ping_handler())

            return True

        except Exception as err:
            _LOGGER.error("Authentication error: %s", err)
            return False

    async def _establish_connection(self) -> bool:
        """Establish the initial TCP connection.

        Returns:
            bool: True if connection was successful, False otherwise
        """
        try:
            _LOGGER.info(
                "Attempting TCP connection to %s:%d with %ds timeout",
                self.config.host,
                self.config.port,
                self.config.timeout,
            )

            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.config.host, self.config.port),
                timeout=self.config.timeout,
            )

            _LOGGER.info(
                "TCP connection established to %s:%d",
                self.config.host,
                self.config.port,
            )
            return True

        except asyncio.TimeoutError as err:
            _LOGGER.error(
                "Connection timeout to %s:%d after %ds - check if device is reachable and port %d is open",
                self.config.host,
                self.config.port,
                self.config.timeout,
                self.config.port,
            )
            return False
        except ConnectionRefusedError as err:
            _LOGGER.error(
                "Connection refused to %s:%d - check if control protocol is enabled on device",
                self.config.host,
                self.config.port,
            )
            return False
        except (ConnectionError, OSError) as err:
            _LOGGER.error(
                "Network error connecting to %s:%d: %s - check network connectivity",
                self.config.host,
                self.config.port,
                err,
            )
            return False

    async def connect(self) -> bool:
        """Connect to the device and authenticate using RackLink protocol."""
        async with self._connection_lock:
            if (
                self._connected
                and self._authenticated
                and self._writer
                and not self._writer.is_closing()
            ):
                _LOGGER.debug("Already connected and authenticated")
                return True

            try:
                _LOGGER.info(
                    "Connecting to RackLink device at %s:%d",
                    self.config.host,
                    self.config.port,
                )

                if not await self._establish_connection():
                    return False

                if not await self._handle_authentication():
                    await self._cleanup_connection()
                    return False

                self._connected = True
                _LOGGER.info(
                    "Successfully connected and authenticated to %s:%d",
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
        # Stop ping task
        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass

        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except (ConnectionError, OSError) as err:
                _LOGGER.debug("Error during connection cleanup: %s", err)

        self._reader = None
        self._writer = None
        self._connected = False
        self._authenticated = False
        self._ping_task = None

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        async with self._connection_lock:
            await self._cleanup_connection()
            _LOGGER.debug("Disconnected from %s:%d", self.config.host, self.config.port)

    async def _read_message(self, timeout: float = 10.0) -> Optional[bytes]:
        """Read a complete RackLink message from the device.

        Args:
            timeout: Timeout in seconds

        Returns:
            bytes: The data envelope (without header, length, checksum, tail) or None if failed
        """
        if not self._reader:
            return None

        try:
            # Read header
            header_byte = await asyncio.wait_for(
                self._reader.readexactly(1), timeout=timeout
            )
            if header_byte[0] != HEADER_BYTE:
                _LOGGER.warning("Invalid header byte: 0x%02X", header_byte[0])
                return None

            # Read length
            length_byte = await asyncio.wait_for(
                self._reader.readexactly(1), timeout=timeout
            )
            length = length_byte[0]

            # Read data envelope
            data_envelope = await asyncio.wait_for(
                self._reader.readexactly(length), timeout=timeout
            )

            # Read checksum and tail
            checksum_tail = await asyncio.wait_for(
                self._reader.readexactly(2), timeout=timeout
            )

            # Verify checksum
            message_for_checksum = header_byte + length_byte + data_envelope
            expected_checksum = sum(message_for_checksum) & 0x7F
            actual_checksum = checksum_tail[0]

            if expected_checksum != actual_checksum:
                _LOGGER.warning(
                    "Checksum mismatch: expected 0x%02X, got 0x%02X",
                    expected_checksum,
                    actual_checksum,
                )
                return None

            # Verify tail
            if checksum_tail[1] != TAIL_BYTE:
                _LOGGER.warning("Invalid tail byte: 0x%02X", checksum_tail[1])
                return None

            # Process escape characters in data envelope
            unescaped_data = self._unescape_data(data_envelope)

            _LOGGER.debug("Received message: %s", unescaped_data.hex())
            return unescaped_data

        except asyncio.TimeoutError:
            _LOGGER.debug("Timeout reading message")
            return None
        except (ConnectionError, OSError) as err:
            _LOGGER.error("Connection error during message read: %s", err)
            raise

    def _unescape_data(self, data: bytes) -> bytes:
        """Remove escape characters from data."""
        result = bytearray()
        i = 0
        while i < len(data):
            if data[i] == ESCAPE_BYTE and i + 1 < len(data):
                # Next byte is escaped, invert its bits
                result.append(data[i + 1] ^ 0xFF)
                i += 2
            else:
                result.append(data[i])
                i += 1
        return bytes(result)

    async def _send_raw_data(self, data: bytes) -> None:
        """Send raw binary data to the device."""
        if not self._writer:
            raise ConnectionError("Not connected to device")

        try:
            self._writer.write(data)
            await self._writer.drain()
        except (ConnectionError, OSError) as err:
            _LOGGER.error("Error sending data: %s", err)
            raise

    async def _ping_handler(self) -> None:
        """Handle incoming ping messages and respond with pong."""
        while self._connected and self._authenticated:
            try:
                # Wait for incoming message
                message = await self._read_message(timeout=30.0)
                if not message:
                    continue

                if len(message) >= 2:
                    command = message[0]
                    subcommand = message[1]

                    # Handle ping message
                    if command == CMD_PING and subcommand == SUBCMD_PING:
                        _LOGGER.debug("Received PING, sending PONG")
                        pong_msg = RackLinkMessage(CMD_PING, SUBCMD_PONG)
                        await self._send_raw_data(pong_msg.build())
                        self._last_ping_time = time.time()

            except Exception as err:
                _LOGGER.error("Error in ping handler: %s", err)
                await asyncio.sleep(1)

    async def send_outlet_command(
        self, outlet: int, state: int, cycle_time: int = 0
    ) -> bool:
        """Send outlet control command.

        Args:
            outlet: Outlet number (1-based)
            state: OUTLET_OFF, OUTLET_ON, or OUTLET_CYCLE
            cycle_time: Cycle time in seconds (for OUTLET_CYCLE)

        Returns:
            bool: True if command successful, False otherwise
        """
        async with self._command_lock:
            if not self._connected or not self._authenticated:
                if not await self.connect():
                    return False

            try:
                # Build command data
                if state == OUTLET_CYCLE:
                    # For cycle command, include cycle time in ASCII format
                    cycle_time_str = f"{cycle_time:04d}"
                    data = bytes([state, outlet]) + cycle_time_str.encode("ascii")
                else:
                    # For on/off commands
                    data = bytes([state, outlet])

                # Build message
                msg = RackLinkMessage(CMD_OUTLET, SUBCMD_OUTLET_SET, data)
                message_bytes = msg.build()

                _LOGGER.debug(
                    "Sending outlet command: outlet=%d, state=%d, data=%s",
                    outlet,
                    state,
                    message_bytes.hex(),
                )
                await self._send_raw_data(message_bytes)

                # Wait for response
                response = await self._read_message()
                if not response:
                    _LOGGER.warning("No response to outlet command")
                    return False

                # Check for NACK
                if len(response) >= 2 and response[0] == CMD_NACK:
                    error_code = response[1] if len(response) > 1 else 0x00
                    _LOGGER.warning(
                        "Outlet command failed with NACK: 0x%02X", error_code
                    )
                    return False

                _LOGGER.debug("Outlet command successful")
                return True

            except Exception as err:
                _LOGGER.error("Error sending outlet command: %s", err)
                return False

    async def read_outlet_state(self, outlet: int) -> Optional[bool]:
        """Read the state of an outlet.

        Args:
            outlet: Outlet number (1-based)

        Returns:
            bool: True if outlet is on, False if off, None if error
        """
        async with self._command_lock:
            if not self._connected or not self._authenticated:
                if not await self.connect():
                    return None

            try:
                # Build read state command
                data = bytes([outlet])
                msg = RackLinkMessage(CMD_OUTLET, SUBCMD_OUTLET_GET, data)
                message_bytes = msg.build()

                _LOGGER.debug(
                    "Reading outlet %d state: %s", outlet, message_bytes.hex()
                )
                await self._send_raw_data(message_bytes)

                # Wait for response
                response = await self._read_message()
                if not response:
                    _LOGGER.warning("No response to outlet state query")
                    return None

                # Check for NACK
                if len(response) >= 2 and response[0] == CMD_NACK:
                    error_code = response[1] if len(response) > 1 else 0x00
                    _LOGGER.warning(
                        "Outlet state query failed with NACK: 0x%02X", error_code
                    )
                    return None

                # Parse response (expecting command, subcommand, outlet, state)
                if (
                    len(response) >= 4
                    and response[0] == CMD_OUTLET
                    and response[1] == SUBCMD_OUTLET_GET
                ):
                    outlet_state = response[3]  # Fourth byte is the state
                    return outlet_state == OUTLET_ON

                _LOGGER.warning(
                    "Unexpected response format for outlet state: %s", response.hex()
                )
                return None

            except Exception as err:
                _LOGGER.error("Error reading outlet state: %s", err)
                return None

    async def test_port_connectivity(self, port: int, timeout: int = 5) -> bool:
        """Test if a specific port is open and responsive.

        Args:
            port: Port number to test
            timeout: Connection timeout in seconds

        Returns:
            bool: True if port is accessible, False otherwise
        """
        try:
            _LOGGER.debug("Testing connectivity to %s:%d", self.config.host, port)

            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.config.host, port), timeout=timeout
            )

            # Clean up connection
            writer.close()
            await writer.wait_closed()

            _LOGGER.debug("Port %d is accessible on %s", port, self.config.host)
            return True

        except Exception as err:
            _LOGGER.debug(
                "Port %d not accessible on %s: %s", port, self.config.host, err
            )
            return False

    async def discover_racklink_port(self) -> Optional[int]:
        """Discover which port the RackLink device is using.

        Tests common RackLink ports to find the correct one.

        Returns:
            int: Discovered port number, or None if not found
        """
        # Common RackLink ports to test
        ports_to_test = [
            60000,  # Standard binary protocol port
            23,  # Telnet (older implementations)
            6000,  # Sometimes misconfigured
            4001,  # Alternative port
            80,  # HTTP (device present but wrong protocol)
            443,  # HTTPS (device present but wrong protocol)
        ]

        _LOGGER.info("Discovering RackLink device port on %s", self.config.host)

        for port in ports_to_test:
            if await self.test_port_connectivity(port):
                _LOGGER.info("Found responsive port %d on %s", port, self.config.host)
                return port

        _LOGGER.warning("No responsive ports found on %s", self.config.host)
        return None

    # Legacy method for compatibility with existing controller code
    async def send_command(self, command: str) -> str:
        """Legacy text command method - converts to appropriate binary commands.

        This method provides compatibility with the existing controller code
        that expects text-based commands.
        """
        _LOGGER.warning(
            "Legacy text command called: %s - converting to binary", command
        )

        # For now, return empty string - the controller will need to be updated
        # to use the new binary methods directly
        return ""
