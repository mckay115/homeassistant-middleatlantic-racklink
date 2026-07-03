"""Socket connection handler for Middle Atlantic RackLink devices.

Supports both the framed binary protocol (Premium+ series, typically port
60000) and the text Telnet-style CLI (Select/Premium series, typically port
6000 or 23) over a single asyncio stream connection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import asyncio
import logging
import re
import time

from .exceptions import RacklinkAuthenticationError, RacklinkConnectionError

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

    def __init__(self, command: int, subcommand: int, data: bytes = b"") -> None:
        self.command = command
        self.subcommand = subcommand
        self.data = data

    def build(self) -> bytes:
        """Build the complete message with header, length, checksum, and tail."""
        data_envelope = bytes([self.command, self.subcommand]) + self.data
        length = len(data_envelope)

        message = bytes([HEADER_BYTE, length]) + data_envelope
        checksum = self._calculate_checksum(message) & 0x7F
        message = message + bytes([checksum, TAIL_BYTE])

        return self._escape_message(message)

    def _calculate_checksum(self, data: bytes) -> int:
        """Calculate checksum as sum of all bytes masked with 0x7F."""
        return sum(data) & 0x7F

    def _escape_message(self, message: bytes) -> bytes:
        """Apply escape characters to message body (excluding header and tail)."""
        result = bytearray()

        for i, byte in enumerate(message):
            if i == 0 or i == len(message) - 1:
                # Header and tail are never escaped
                result.append(byte)
            elif byte in (HEADER_BYTE, TAIL_BYTE, ESCAPE_BYTE):
                result.append(ESCAPE_BYTE)
                result.append(byte ^ 0xFF)  # Invert bits
            else:
                result.append(byte)

        return bytes(result)


class SocketConnection:
    """Socket connection manager for Middle Atlantic RackLink devices."""

    def __init__(self, config: SocketConfig) -> None:
        """Initialize the socket connection."""
        self.config = config
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False
        self._authenticated = False
        self._connection_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()
        self._ping_task: Optional[asyncio.Task] = None
        self._last_ping_time = 0.0
        self._protocol_type: Optional[str] = None
        self._initial_data: bytes = b""
        self._outlet_names: Dict[int, str] = {}

    @property
    def connected(self) -> bool:
        """Return True if connected to the device."""
        return self._connected

    @property
    def authenticated(self) -> bool:
        """Return True if authenticated with the device."""
        return self._authenticated

    @property
    def protocol_type(self) -> Optional[str]:
        """Return the detected protocol type ('telnet' or 'binary')."""
        return self._protocol_type

    @property
    def outlet_names(self) -> Dict[int, str]:
        """Return outlet names parsed from the latest telnet response."""
        return self._outlet_names

    def _require_reader(self) -> asyncio.StreamReader:
        """Return the active stream reader.

        Raises:
            RacklinkConnectionError: If the socket is not connected.
        """
        if self._reader is None:
            raise RacklinkConnectionError("Socket is not connected")
        return self._reader

    def _require_writer(self) -> asyncio.StreamWriter:
        """Return the active stream writer.

        Raises:
            RacklinkConnectionError: If the socket is not connected.
        """
        if self._writer is None:
            raise RacklinkConnectionError("Socket is not connected")
        return self._writer

    async def _handle_authentication(self) -> bool:
        """Handle authentication based on detected protocol type.

        Raises:
            RacklinkAuthenticationError: If the device rejects the credentials.

        Returns:
            bool: True if authentication was successful, False otherwise.
        """
        if not (self.config.username and self.config.password):
            _LOGGER.error("Username and password required for authentication")
            return False

        # Detect protocol type using existing connection
        if self._protocol_type is None:
            self._protocol_type, self._initial_data = (
                await self._detect_protocol_from_connection()
            )

        _LOGGER.debug("Using %s protocol for authentication", self._protocol_type)

        if self._protocol_type == "telnet":
            return await self._handle_telnet_authentication()
        if self._protocol_type == "binary":
            return await self._handle_binary_authentication()

        _LOGGER.error("Unknown protocol type: %s", self._protocol_type)
        return False

    async def _detect_protocol_from_connection(self) -> Tuple[str, bytes]:
        """Detect protocol type using the existing connection.

        Returns:
            tuple: (protocol_type, initial_data) where protocol_type is
            'telnet', 'binary', or 'unknown'.
        """
        try:
            _LOGGER.debug("Detecting protocol type on existing connection")

            try:
                initial_response = await asyncio.wait_for(
                    self._require_reader().read(1024), timeout=3.0
                )

                if initial_response:
                    response_text = initial_response.decode("utf-8", errors="ignore")

                    # Check for Telnet IAC sequences or login prompts
                    if any(
                        seq in initial_response
                        for seq in (b"\xff\xfb", b"\xff\xfd", b"\xff\xfe")
                    ) or any(
                        keyword in response_text.lower()
                        for keyword in (
                            "login",
                            "username",
                            "password",
                            "racklink",
                            "cli",
                        )
                    ):
                        _LOGGER.debug(
                            "Detected Telnet protocol (IAC sequences or login prompt)"
                        )
                        return "telnet", initial_response

                _LOGGER.debug("No Telnet indicators, assuming binary protocol")
                return "binary", initial_response

            except asyncio.TimeoutError:
                _LOGGER.debug("No initial response, assuming binary protocol")
                return "binary", b""

        except Exception as err:
            _LOGGER.error("Error detecting protocol: %s", err)
            return "unknown", b""

    async def _handle_binary_authentication(self) -> bool:
        """Handle RackLink binary protocol authentication.

        Raises:
            RacklinkAuthenticationError: If the device rejects the credentials.

        Returns:
            bool: True if authentication was successful, False otherwise.
        """
        try:
            credentials = f"{self.config.username}|{self.config.password}"
            login_msg = RackLinkMessage(
                CMD_LOGIN, SUBCMD_LOGIN, credentials.encode("ascii")
            )

            _LOGGER.debug("Sending binary login message")
            await self._send_raw_data(login_msg.build())

            try:
                response = await asyncio.wait_for(self._read_message(), timeout=5.0)
                if not response:
                    _LOGGER.error(
                        "No response to binary login command - device may not "
                        "support binary protocol"
                    )
                    return False
            except asyncio.TimeoutError:
                _LOGGER.error(
                    "Timeout waiting for binary login response - device likely "
                    "uses Telnet protocol"
                )
                return False

            # Check for NACK (error response)
            if len(response) >= 2 and response[0] == CMD_NACK:
                error_code = response[1]
                if error_code in (NACK_INVALID_CREDENTIALS, NACK_ACCESS_DENIED):
                    raise RacklinkAuthenticationError(
                        "Binary authentication failed: invalid credentials"
                    )
                _LOGGER.error(
                    "Binary authentication failed: NACK error code 0x%02X",
                    error_code,
                )
                return False

            _LOGGER.debug("RackLink binary authentication successful")
            self._authenticated = True

            # Start ping handler to answer keepalive PINGs from the device
            self._ping_task = asyncio.create_task(self._ping_handler())

            return True

        except RacklinkAuthenticationError:
            raise
        except Exception as err:
            _LOGGER.error("Binary authentication error: %s", err)
            return False

    async def _establish_connection(self) -> bool:
        """Establish the initial TCP connection."""
        try:
            _LOGGER.debug(
                "Attempting TCP connection to %s:%d with %ds timeout",
                self.config.host,
                self.config.port,
                self.config.timeout,
            )

            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.config.host, self.config.port),
                timeout=self.config.timeout,
            )

            _LOGGER.debug(
                "TCP connection established to %s:%d",
                self.config.host,
                self.config.port,
            )
            return True

        except asyncio.TimeoutError:
            _LOGGER.error(
                "Connection timeout to %s:%d after %ds - check if device is "
                "reachable and port is open",
                self.config.host,
                self.config.port,
                self.config.timeout,
            )
            return False
        except ConnectionRefusedError:
            _LOGGER.error(
                "Connection refused to %s:%d - check if control protocol is "
                "enabled on device",
                self.config.host,
                self.config.port,
            )
            return False
        except (ConnectionError, OSError) as err:
            _LOGGER.error(
                "Network error connecting to %s:%d: %s",
                self.config.host,
                self.config.port,
                err,
            )
            return False

    async def connect(self) -> bool:
        """Connect to the device and authenticate using RackLink protocol.

        Raises:
            RacklinkAuthenticationError: If the device rejects the credentials.
        """
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
                _LOGGER.debug(
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
                    "Connected and authenticated to %s:%d (%s protocol)",
                    self.config.host,
                    self.config.port,
                    self._protocol_type,
                )
                return True

            except RacklinkAuthenticationError:
                await self._cleanup_connection()
                raise
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
        # Force re-detection on next connect; the device may expose a
        # different protocol after a reboot or firmware change.
        self._protocol_type = None
        self._initial_data = b""

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        async with self._connection_lock:
            await self._cleanup_connection()
            _LOGGER.debug("Disconnected from %s:%d", self.config.host, self.config.port)

    async def reconnect(self) -> bool:
        """Attempt to reconnect to the device."""
        _LOGGER.info(
            "Attempting to reconnect to %s:%d", self.config.host, self.config.port
        )

        if self._connected:
            await self.disconnect()

        return await self.connect()

    async def ensure_connected(self) -> bool:
        """Ensure we are connected, reconnecting if necessary."""
        if not self._connected or not self._authenticated:
            _LOGGER.info(
                "Connection lost or not authenticated, attempting to reconnect"
            )
            return await self.reconnect()
        return True

    def _mark_disconnected(self) -> None:
        """Mark the connection as lost so the next call reconnects."""
        self._connected = False
        self._authenticated = False

    async def _read_unescaped_byte(self, timeout: float) -> int:
        """Read a single logical byte from the stream, resolving escapes."""
        byte = (await asyncio.wait_for(self._require_reader().readexactly(1), timeout=timeout))[0]
        if byte == ESCAPE_BYTE:
            escaped = (
                await asyncio.wait_for(self._require_reader().readexactly(1), timeout=timeout)
            )[0]
            return escaped ^ 0xFF
        return byte

    async def _read_message(self, timeout: float = 10.0) -> Optional[bytes]:
        """Read a complete RackLink message from the device.

        The sender escapes every byte between the header and tail (including
        the length and checksum bytes), so all of those must be read as
        logical (unescaped) bytes.

        Returns:
            bytes: The data envelope (without header, length, checksum, tail)
            or None if the frame was invalid or timed out.
        """
        if not self._reader:
            return None

        try:
            # Header is never escaped
            header = (
                await asyncio.wait_for(self._require_reader().readexactly(1), timeout=timeout)
            )[0]
            if header != HEADER_BYTE:
                _LOGGER.warning("Invalid header byte: 0x%02X", header)
                return None

            length = await self._read_unescaped_byte(timeout)

            data_envelope = bytes(
                [await self._read_unescaped_byte(timeout) for _ in range(length)]
            )

            checksum = await self._read_unescaped_byte(timeout)

            # Tail is never escaped
            tail = (
                await asyncio.wait_for(self._require_reader().readexactly(1), timeout=timeout)
            )[0]

            expected_checksum = (header + length + sum(data_envelope)) & 0x7F
            if expected_checksum != checksum:
                _LOGGER.warning(
                    "Checksum mismatch: expected 0x%02X, got 0x%02X",
                    expected_checksum,
                    checksum,
                )
                return None

            if tail != TAIL_BYTE:
                _LOGGER.warning("Invalid tail byte: 0x%02X", tail)
                return None

            _LOGGER.debug("Received message: %s", data_envelope.hex())
            return data_envelope

        except asyncio.TimeoutError:
            _LOGGER.debug("Timeout reading message")
            return None
        except asyncio.IncompleteReadError as err:
            _LOGGER.error("Connection closed during message read: %s", err)
            self._mark_disconnected()
            raise ConnectionError("Connection closed by device") from err
        except (ConnectionError, OSError) as err:
            _LOGGER.error("Connection error during message read: %s", err)
            self._mark_disconnected()
            raise

    async def _read_response(self, timeout: float = 10.0) -> Optional[bytes]:
        """Read the next non-PING message, answering PINGs inline.

        The device sends keepalive PINGs on the same stream that command
        responses arrive on, so a command may receive a PING before its
        actual response.
        """
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            message = await self._read_message(timeout=remaining)
            if message is None:
                return None
            if (
                len(message) >= 2
                and message[0] == CMD_PING
                and message[1] == SUBCMD_PING
            ):
                _LOGGER.debug("Received PING while awaiting response, sending PONG")
                await self._send_raw_data(RackLinkMessage(CMD_PING, SUBCMD_PONG).build())
                self._last_ping_time = time.time()
                continue
            return message

    async def _send_raw_data(self, data: bytes) -> None:
        """Send raw binary data to the device."""
        if not self._writer:
            raise ConnectionError("Not connected to device")

        try:
            self._require_writer().write(data)
            await self._require_writer().drain()
        except (ConnectionError, OSError) as err:
            _LOGGER.error("Error sending data: %s", err)
            self._mark_disconnected()
            raise

    async def _ping_handler(self) -> None:
        """Answer keepalive PING messages from the device.

        Holds the command lock while reading so it never races a command
        transaction for frames on the shared stream.
        """
        while self._connected or self._authenticated:
            try:
                async with self._command_lock:
                    if not self._connected and not self._authenticated:
                        break
                    message = await self._read_message(timeout=1.0)
                    if (
                        message
                        and len(message) >= 2
                        and message[0] == CMD_PING
                        and message[1] == SUBCMD_PING
                    ):
                        _LOGGER.debug("Received PING, sending PONG")
                        await self._send_raw_data(
                            RackLinkMessage(CMD_PING, SUBCMD_PONG).build()
                        )
                        self._last_ping_time = time.time()
                # Yield so queued commands can grab the lock
                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                raise
            except (ConnectionError, OSError):
                _LOGGER.warning("Connection lost in ping handler")
                self._mark_disconnected()
                break
            except Exception as err:
                _LOGGER.error("Error in ping handler: %s", err)
                await asyncio.sleep(1)

    async def send_outlet_command(
        self, outlet: int, state: int, cycle_time: int = 0
    ) -> bool:
        """Send outlet control command via the binary protocol.

        Args:
            outlet: Outlet number (1-based)
            state: OUTLET_OFF, OUTLET_ON, or OUTLET_CYCLE
            cycle_time: Cycle time in seconds (for OUTLET_CYCLE)

        Returns:
            bool: True if command successful, False otherwise
        """
        if not self._connected or not self._authenticated:
            if not await self.connect():
                return False

        async with self._command_lock:
            try:
                if state == OUTLET_CYCLE:
                    cycle_time_str = f"{cycle_time:04d}"
                    data = bytes([state, outlet]) + cycle_time_str.encode("ascii")
                else:
                    data = bytes([state, outlet])

                msg = RackLinkMessage(CMD_OUTLET, SUBCMD_OUTLET_SET, data)

                _LOGGER.debug(
                    "Sending outlet command: outlet=%d, state=%d", outlet, state
                )
                await self._send_raw_data(msg.build())

                response = await self._read_response()
                if not response:
                    _LOGGER.warning("No response to outlet command")
                    return False

                if len(response) >= 2 and response[0] == CMD_NACK:
                    _LOGGER.warning(
                        "Outlet command failed with NACK: 0x%02X", response[1]
                    )
                    return False

                _LOGGER.debug("Outlet command successful")
                return True

            except (ConnectionError, OSError) as err:
                _LOGGER.error("Error sending outlet command: %s", err)
                return False

    async def read_outlet_state(self, outlet: int) -> Optional[bool]:
        """Read the state of an outlet via the binary protocol.

        Args:
            outlet: Outlet number (1-based)

        Returns:
            bool: True if outlet is on, False if off, None if error
        """
        if not self._connected or not self._authenticated:
            if not await self.connect():
                return None

        async with self._command_lock:
            try:
                msg = RackLinkMessage(CMD_OUTLET, SUBCMD_OUTLET_GET, bytes([outlet]))

                _LOGGER.debug("Reading outlet %d state", outlet)
                await self._send_raw_data(msg.build())

                response = await self._read_response()
                if not response:
                    _LOGGER.debug("No response to outlet state query")
                    return None

                if len(response) >= 2 and response[0] == CMD_NACK:
                    _LOGGER.warning(
                        "Outlet state query failed with NACK: 0x%02X", response[1]
                    )
                    return None

                # Expecting command, subcommand, outlet, state
                if (
                    len(response) >= 4
                    and response[0] == CMD_OUTLET
                    and response[1] == SUBCMD_OUTLET_GET
                ):
                    return response[3] == OUTLET_ON

                _LOGGER.warning(
                    "Unexpected response format for outlet state: %s", response.hex()
                )
                return None

            except (ConnectionError, OSError) as err:
                _LOGGER.error("Error reading outlet state: %s", err)
                return None

    async def send_telnet_command(self, command: str) -> str:
        """Send a command via Telnet protocol (for Select/Premium series).

        Args:
            command: The command to send (e.g., "show outlets all")

        Returns:
            str: The response from the device
        """
        # Serialize Telnet access to prevent concurrent reads/writes
        async with self._command_lock:
            if not self._reader or not self._writer:
                _LOGGER.error("Telnet not connected - cannot send command")
                self._mark_disconnected()
                return ""

            try:
                # Check if session is corrupted (stuck in command mode)
                if await self._is_session_corrupted():
                    _LOGGER.warning("Session corruption detected, attempting recovery")
                    if not await self._recover_telnet_session():
                        _LOGGER.error("Failed to recover Telnet session")
                        return ""

                # Clear any buffered input before sending command
                await self._flush_input_buffer()

                full_command = f"{command}\r\n"
                _LOGGER.debug("Sending Telnet command: %s", command)

                self._require_writer().write(full_command.encode("ascii"))
                await self._require_writer().drain()

                response_parts = []
                corruption_detected = False
                connection_closed = False
                start_time = time.time()

                while True:
                    try:
                        data = await asyncio.wait_for(
                            self._require_reader().read(1024), timeout=3.0
                        )
                        if not data:
                            # EOF: the device closed the connection
                            connection_closed = True
                            break

                        text = data.decode("utf-8", errors="ignore")
                        response_parts.append(text)

                        # Check for REAL corruption indicators (not normal responses)
                        corruption_indicators = [
                            text.count("^") > 1,
                            len(text) > 500
                            and "show" in text
                            and text.count("show") > 3,
                            "(1/2/3/4/5/6/7/8/all)" in text and "] # " not in text,
                            "label  Outlet label" in text and "] # " not in text,
                            text.count(command) > 2 if command else False,
                        ]

                        if any(corruption_indicators):
                            corruption_detected = True
                            _LOGGER.warning("Corruption detected: %s", text[:150])
                            break

                        # End of response when prompt appears
                        if "] # " in text or "> " in text or "$ " in text:
                            break

                        if time.time() - start_time > 8.0:
                            _LOGGER.warning("Command timeout after 8 seconds")
                            break

                    except asyncio.TimeoutError:
                        _LOGGER.debug(
                            "Timeout reading Telnet response for '%s'", command
                        )
                        break

                if connection_closed:
                    _LOGGER.warning(
                        "Telnet connection closed by device during command '%s'",
                        command,
                    )
                    self._mark_disconnected()
                    return ""

                if corruption_detected:
                    _LOGGER.error(
                        "Command '%s' failed due to session corruption", command
                    )
                    await self._recover_telnet_session()
                    return ""

                full_response = "".join(response_parts)
                result = self._clean_telnet_response(full_response, command)

                if result and "Unknown command" in result:
                    _LOGGER.debug(
                        "Command '%s' not recognized by device: %s",
                        command,
                        result.strip(),
                    )
                elif result:
                    _LOGGER.debug(
                        "Command '%s' completed, response length: %d",
                        command,
                        len(result),
                    )
                else:
                    _LOGGER.debug("Command '%s' returned empty response", command)

                return result

            except (ConnectionError, OSError, asyncio.TimeoutError) as err:
                _LOGGER.error("Connection error during command '%s': %s", command, err)
                self._mark_disconnected()
                return ""

    async def _is_session_corrupted(self) -> bool:
        """Check if the Telnet session is corrupted (stuck in command mode)."""
        try:
            self._require_writer().write(b"\r\n")
            await self._require_writer().drain()

            try:
                data = await asyncio.wait_for(self._require_reader().read(512), timeout=1.0)
                response = data.decode("utf-8", errors="ignore")

                corruption_indicators = [
                    "label  Outlet label" in response,
                    "(1/2/3/4/5/6/7/8/all)" in response,
                    "^\r\n" in response,
                    # Long command history indicates buffer overflow
                    len(response) > 200
                    and "show" in response
                    and "outlet" in response,
                ]

                if any(corruption_indicators):
                    _LOGGER.warning(
                        "Session corruption detected. Response: %s", response[:200]
                    )
                    return True

            except asyncio.TimeoutError:
                # No immediate response is actually good - means we're at prompt
                pass

            return False

        except (ConnectionError, OSError) as err:
            _LOGGER.debug("Error checking session corruption: %s", err)
            self._mark_disconnected()
            return True

    async def _recover_telnet_session(self) -> bool:
        """Attempt to recover a corrupted Telnet session."""
        try:
            _LOGGER.info("Attempting Telnet session recovery")

            recovery_sequences = [
                b"\x03\r\n",  # Ctrl+C to cancel any pending command
                b"\x1b\r\n",  # ESC to cancel
                b"\r\n\r\n\r\n",  # Multiple enters to get back to prompt
            ]

            for sequence in recovery_sequences:
                _LOGGER.debug("Trying recovery sequence: %r", sequence)
                self._require_writer().write(sequence)
                await self._require_writer().drain()

                await asyncio.sleep(0.5)

                try:
                    while True:
                        data = await asyncio.wait_for(
                            self._require_reader().read(1024), timeout=0.5
                        )
                        if not data:
                            break
                        response = data.decode("utf-8", errors="ignore")

                        # Check if we got back to a clean prompt
                        if "] # " in response and "label" not in response:
                            _LOGGER.info("Session recovery successful")
                            return True

                except asyncio.TimeoutError:
                    pass

            # If recovery sequences didn't work, try full reconnection
            _LOGGER.warning("Recovery sequences failed, attempting full reconnection")
            await self._cleanup_connection()
            await asyncio.sleep(1.0)

            if await self.connect():
                _LOGGER.info("Full reconnection successful")
                return True

            _LOGGER.error("Full reconnection failed")
            return False

        except (ConnectionError, OSError) as err:
            _LOGGER.error("Error during session recovery: %s", err)
            self._mark_disconnected()
            return False

    async def _flush_input_buffer(self) -> None:
        """Flush any pending input to clear buffer before sending commands."""
        try:
            while True:
                try:
                    data = await asyncio.wait_for(self._require_reader().read(1024), timeout=0.1)
                    if not data:
                        break
                    _LOGGER.debug("Flushed %d bytes from input buffer", len(data))
                except asyncio.TimeoutError:
                    break
        except (ConnectionError, OSError) as err:
            _LOGGER.debug("Error flushing input buffer: %s", err)

    def _clean_telnet_response(self, response: str, command: str) -> str:
        """Clean up Telnet response by removing command echo and prompts."""
        if not response:
            return ""

        lines = response.split("\n")
        cleaned_lines = []

        for line in lines:
            line = line.strip()

            if not line:
                continue

            # Skip command echo (exact match or contains the command)
            if line == command or command in line:
                continue

            # Skip prompts and navigation
            if any(
                prompt in line for prompt in ("] # ", "> ", "$ ", "login:", "password:")
            ):
                continue

            # Skip REAL corruption indicators (but keep normal error responses)
            corruption_indicators = [
                line.count("^") > 1,
                "(1/2/3/4/5/6/7/8/all)" in line and "Unknown command" not in line,
                "label  Outlet label" in line and "Unknown command" not in line,
            ]

            if any(corruption_indicators):
                continue

            cleaned_lines.append(line)

        return "\n".join(cleaned_lines).strip()

    async def telnet_outlet_command(self, outlet: int, action: str) -> bool:
        """Send outlet control command via Telnet.

        Args:
            outlet: Outlet number (1-based)
            action: 'on', 'off', or 'cycle'

        Returns:
            bool: True if command succeeded
        """
        command = f"power outlets {outlet} {action} /y"
        _LOGGER.debug("Sending Telnet outlet command: %s", command)
        response = await self.send_telnet_command(command)

        if not self._connected:
            return False

        # For outlet commands, success is typically indicated by getting back
        # to the prompt without error messages
        if "error" not in response.lower() and "invalid" not in response.lower():
            _LOGGER.debug("Telnet outlet %d %s command successful", outlet, action)
            return True

        _LOGGER.warning(
            "Telnet outlet %d %s command may have failed: %s",
            outlet,
            action,
            response,
        )
        return False

    async def telnet_read_outlet_states(self) -> Dict[int, bool]:
        """Read all outlet states via Telnet.

        Returns:
            Dict mapping outlet numbers to state (True=On, False=Off)
        """
        _LOGGER.debug("Sending 'show outlets all' command")
        response = await self.send_telnet_command("show outlets all")

        outlet_states: Dict[int, bool] = {}

        # Parse response using exact format from device output:
        # Outlet 1 - Firewall:
        # Power state: On
        pattern = r"Outlet (\d+) - ([^:]+):\s*\n(?:.*?\n)*?Power state:\s*(On|Off)"
        matches = re.findall(pattern, response, re.MULTILINE | re.DOTALL)

        for outlet_str, outlet_name, state_str in matches:
            outlet_num = int(outlet_str)
            outlet_states[outlet_num] = state_str.lower() == "on"
            self._outlet_names[outlet_num] = outlet_name.strip()

        _LOGGER.debug("Read %d outlet states via Telnet", len(outlet_states))
        return outlet_states

    async def _handle_telnet_authentication(self) -> bool:
        """Handle Telnet authentication sequence.

        Raises:
            RacklinkAuthenticationError: If the device rejects the credentials.

        Returns:
            bool: True if authentication successful
        """
        if not (self.config.username and self.config.password):
            _LOGGER.error("Username and password required for Telnet authentication")
            return False

        try:
            # Send username
            self._require_writer().write(f"{self.config.username}\r\n".encode())
            await self._require_writer().drain()

            # Read response (should ask for password)
            await asyncio.wait_for(self._require_reader().read(1024), timeout=5.0)

            # Send password
            self._require_writer().write(f"{self.config.password}\r\n".encode())
            await self._require_writer().drain()

            # Read final authentication response
            auth_response = await asyncio.wait_for(self._require_reader().read(1024), timeout=5.0)
            auth_text = auth_response.decode("utf-8", errors="ignore")

            # Check for successful login (welcome message or command prompt)
            success_indicators = [
                "welcome",
                "last login",
                "] # ",
                "> ",
                "$ ",
                "cli>",
                "racklink",
                "ma>",
                "slot",  # Middle Atlantic slot-based responses
                ".wrk",  # Working directory or slot indicators
                "ma-",  # Middle Atlantic prefix
                "pdu",  # PDU responses
            ]
            auth_text_lower = auth_text.lower()

            if any(indicator in auth_text_lower for indicator in success_indicators):
                _LOGGER.debug("Telnet authentication successful")
                self._authenticated = True
                return True

            if any(
                failure in auth_text_lower
                for failure in ("login incorrect", "authentication failed", "denied")
            ) or "login:" in auth_text_lower:
                raise RacklinkAuthenticationError(
                    "Telnet authentication failed: invalid credentials"
                )

            _LOGGER.error(
                "Telnet authentication failed - no success indicators found in "
                "response: %s",
                auth_text[:200],
            )
            return False

        except asyncio.TimeoutError:
            _LOGGER.error("Timeout during Telnet authentication")
            return False
        except RacklinkAuthenticationError:
            raise
        except (ConnectionError, OSError) as err:
            _LOGGER.error("Telnet authentication error: %s", err)
            return False

    async def send_command(self, command: str) -> str:
        """Send a text command, routing to the appropriate protocol.

        Only the Telnet protocol supports text commands; binary-protocol
        devices return an empty string.
        """
        _LOGGER.debug("Text command called: %s", command)

        if not await self.ensure_connected():
            _LOGGER.error("Cannot send command '%s' - connection failed", command)
            return ""

        if self._protocol_type == "telnet":
            return await self.send_telnet_command(command)

        _LOGGER.debug(
            "Command '%s' skipped - binary protocol does not support text commands",
            command,
        )
        return ""
