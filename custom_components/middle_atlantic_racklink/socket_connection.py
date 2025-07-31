"""Socket connection handler for Middle Atlantic RackLink devices using binary protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple, List
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
        """Handle authentication based on detected protocol type.

        Returns:
            bool: True if authentication was successful, False otherwise
        """
        if not (self.config.username and self.config.password):
            _LOGGER.error("Username and password required for authentication")
            return False

        # Detect protocol type using existing connection
        if not hasattr(self, "_protocol_type"):
            self._protocol_type, self._initial_data = await self._detect_protocol_from_connection()

        _LOGGER.info("Using %s protocol for authentication", self._protocol_type)

        if self._protocol_type == "telnet":
            return await self._handle_telnet_authentication()
        elif self._protocol_type == "binary":
            return await self._handle_binary_authentication()
        else:
            _LOGGER.error("Unknown protocol type: %s", self._protocol_type)
            return False

    async def _detect_protocol_from_connection(self) -> tuple:
        """Detect protocol type using the existing connection.
        
        Returns:
            tuple: (protocol_type, initial_data) where protocol_type is 'telnet', 'binary', or 'unknown'
        """
        try:
            _LOGGER.debug("Detecting protocol type using existing connection")
            
            # Read initial response to detect protocol
            try:
                initial_response = await asyncio.wait_for(
                    self._reader.read(1024), timeout=3.0
                )
                
                if initial_response:
                    response_hex = initial_response.hex()
                    response_text = initial_response.decode("utf-8", errors="ignore")
                    
                    _LOGGER.debug("Initial connection response: %s", response_hex)
                    
                    # Check for Telnet IAC sequences or login prompts
                    if any(
                        seq in initial_response
                        for seq in [b"\xff\xfb", b"\xff\xfd", b"\xff\xfe"]
                    ) or any(
                        keyword in response_text.lower()
                        for keyword in ["login", "username", "password", "racklink", "cli"]
                    ):
                        _LOGGER.debug("Detected Telnet protocol (IAC sequences or login prompt)")
                        return "telnet", initial_response
                
                # If we get here, assume binary protocol
                _LOGGER.debug("No Telnet indicators, assuming binary protocol")
                return "binary", initial_response
                
            except asyncio.TimeoutError:
                # No initial response - could be binary protocol
                _LOGGER.debug("No initial response, assuming binary protocol")
                return "binary", b""
                
        except Exception as err:
            _LOGGER.error("Error detecting protocol: %s", err)
            return "unknown", b""

    async def _handle_binary_authentication(self) -> bool:
        """Handle RackLink binary protocol authentication.

        Sends login command with username|password format.

        Returns:
            bool: True if authentication was successful, False otherwise
        """
        try:
            # Create login message: username|password
            credentials = f"{self.config.username}|{self.config.password}"
            login_data = credentials.encode("ascii")

            # Build login message
            login_msg = RackLinkMessage(CMD_LOGIN, SUBCMD_LOGIN, login_data)
            message_bytes = login_msg.build()

            _LOGGER.debug("Sending binary login message: %s", message_bytes.hex())
            await self._send_raw_data(message_bytes)

            # Wait for response
            response = await self._read_message()
            if not response:
                _LOGGER.error("No response to binary login command")
                return False

            # Check for NACK (error response)
            if len(response) >= 2 and response[0] == CMD_NACK:
                error_code = response[1] if len(response) > 1 else 0x00
                if error_code == NACK_INVALID_CREDENTIALS:
                    _LOGGER.error("Binary authentication failed: Invalid credentials")
                elif error_code == NACK_ACCESS_DENIED:
                    _LOGGER.error("Binary authentication failed: Access denied")
                else:
                    _LOGGER.error(
                        "Binary authentication failed: NACK error code 0x%02X",
                        error_code,
                    )
                return False

            # Successful authentication
            _LOGGER.info("RackLink binary authentication successful")
            self._authenticated = True

            # Start ping handler to maintain connection
            self._ping_task = asyncio.create_task(self._ping_handler())

            return True

        except Exception as err:
            _LOGGER.error("Binary authentication error: %s", err)
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

    async def detect_protocol_type(self, port: int, timeout: int = 5) -> str:
        """Detect what protocol a port is using.

        Args:
            port: Port number to test
            timeout: Connection timeout in seconds

        Returns:
            str: 'binary', 'telnet', 'http', or 'unknown'
        """
        try:
            _LOGGER.debug("Detecting protocol type on %s:%d", self.config.host, port)

            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.config.host, port), timeout=timeout
            )

            # Read initial response to detect protocol
            try:
                initial_response = await asyncio.wait_for(
                    reader.read(1024), timeout=2.0
                )

                if initial_response:
                    response_hex = initial_response.hex()
                    response_text = initial_response.decode("utf-8", errors="ignore")

                    _LOGGER.debug("Port %d initial response: %s", port, response_hex)

                    # Check for Telnet IAC sequences
                    if any(
                        seq in initial_response
                        for seq in [b"\xff\xfb", b"\xff\xfd", b"\xff\xfe"]
                    ):
                        writer.close()
                        await writer.wait_closed()
                        _LOGGER.debug(
                            "Port %d detected as Telnet (IAC sequences found)", port
                        )
                        return "telnet"

                    # Check for HTTP responses
                    if (
                        b"HTTP/" in initial_response
                        or b"html" in initial_response.lower()
                    ):
                        writer.close()
                        await writer.wait_closed()
                        _LOGGER.debug("Port %d detected as HTTP", port)
                        return "http"

                    # Check for login prompts (typical for Telnet devices)
                    if any(
                        keyword in response_text.lower()
                        for keyword in ["login", "username", "password", "racklink"]
                    ):
                        writer.close()
                        await writer.wait_closed()
                        _LOGGER.debug("Port %d detected as Telnet (login prompt)", port)
                        return "telnet"

                # If no initial response, try sending a binary test message
                credentials = f"{self.config.username or 'user'}|{self.config.password or 'password'}"
                login_data = credentials.encode("ascii")
                login_msg = RackLinkMessage(CMD_LOGIN, SUBCMD_LOGIN, login_data)
                message_bytes = login_msg.build()

                writer.write(message_bytes)
                await writer.drain()

                # Wait for response
                response = await asyncio.wait_for(reader.read(1024), timeout=3.0)
                if response:
                    _LOGGER.debug("Port %d responded to binary protocol test", port)
                    writer.close()
                    await writer.wait_closed()
                    return "binary"

            except asyncio.TimeoutError:
                # No response to protocol tests
                pass

            writer.close()
            await writer.wait_closed()
            _LOGGER.debug("Port %d protocol type unknown", port)
            return "unknown"

        except Exception as err:
            _LOGGER.debug("Error detecting protocol on port %d: %s", port, err)
            return "unknown"

    async def discover_racklink_port(self) -> Optional[int]:
        """Discover which port the RackLink device is using.

        Tests common RackLink ports to find the correct one.

        Returns:
            int: Discovered port number, or None if not found
        """
        # Common RackLink ports to test - reordered based on device compatibility
        ports_to_test = [
            6000,  # Telnet protocol (Select/Premium series) - try first
            60000,  # Binary protocol (Premium+ series)
            23,  # Standard Telnet port (older implementations)
            4001,  # Alternative port
            80,  # HTTP (device present but wrong protocol)
            443,  # HTTPS (device present but wrong protocol)
        ]

        _LOGGER.info("Discovering RackLink device port on %s", self.config.host)

        # Track ports by protocol type
        working_ports = []

        for port in ports_to_test:
            if await self.test_port_connectivity(port):
                protocol = await self.detect_protocol_type(port)
                _LOGGER.info(
                    "Port %d on %s: protocol=%s", port, self.config.host, protocol
                )

                if protocol in ["telnet", "binary"]:
                    working_ports.append((port, protocol))
                elif protocol == "http":
                    _LOGGER.info(
                        "Port %d is HTTP - device present but need control protocol enabled",
                        port,
                    )

        # Prioritize based on protocol type
        for port, protocol in working_ports:
            if protocol == "telnet":
                _LOGGER.info("Found Telnet RackLink device on port %d", port)
                return port
            elif protocol == "binary":
                _LOGGER.info("Found binary RackLink device on port %d", port)
                return port

        # If we found any working ports, return the first one
        if working_ports:
            port, protocol = working_ports[0]
            _LOGGER.info("Using port %d with protocol %s", port, protocol)
            return port

        _LOGGER.warning("No RackLink-compatible ports found on %s", self.config.host)
        return None

    async def send_telnet_command(self, command: str) -> str:
        """Send a command via Telnet protocol (for Select/Premium series).

        Args:
            command: The command to send (e.g., "show outlets all")

        Returns:
            str: The response from the device
        """
        if not self._reader or not self._writer:
            _LOGGER.error("Telnet not connected - cannot send command")
            return ""

        try:
            # Send command with newline
            full_command = f"{command}\r\n"
            _LOGGER.debug("Sending Telnet command: %s", command)

            self._writer.write(full_command.encode("ascii"))
            await self._writer.drain()

            # Read response until we see the prompt
            response_parts = []
            while True:
                try:
                    data = await asyncio.wait_for(self._reader.read(1024), timeout=5.0)
                    if not data:
                        break

                    text = data.decode("utf-8", errors="ignore")
                    response_parts.append(text)

                    # Look for command prompt indicating end of response
                    if "] # " in text or "> " in text or "$ " in text:
                        break

                except asyncio.TimeoutError:
                    _LOGGER.debug("Timeout reading Telnet response")
                    break

            full_response = "".join(response_parts)

            # Clean up response - remove the command echo and prompt
            lines = full_response.split("\n")
            cleaned_lines = []

            for line in lines:
                # Skip the command echo and prompts
                if command in line or "] # " in line or "> " in line:
                    continue
                cleaned_lines.append(line)

            result = "\n".join(cleaned_lines).strip()
            _LOGGER.debug("Telnet command '%s' response: %s", command, result[:200])
            return result

        except Exception as err:
            _LOGGER.error("Error sending Telnet command '%s': %s", command, err)
            return ""

    async def telnet_outlet_command(self, outlet: int, action: str) -> bool:
        """Send outlet control command via Telnet.

        Args:
            outlet: Outlet number (1-based)
            action: 'on', 'off', or 'cycle'

        Returns:
            bool: True if command succeeded
        """
        try:
            # Format command based on examples from response_samples
            command = f"power outlets {outlet} {action} /y"
            response = await self.send_telnet_command(command)

            # For outlet commands, success is typically indicated by getting back to prompt
            # without error messages
            if "error" not in response.lower() and "invalid" not in response.lower():
                _LOGGER.info("Telnet outlet %d %s command successful", outlet, action)
                return True
            else:
                _LOGGER.warning(
                    "Telnet outlet %d %s command may have failed: %s",
                    outlet,
                    action,
                    response,
                )
                return False

        except Exception as err:
            _LOGGER.error("Error sending Telnet outlet command: %s", err)
            return False

    async def telnet_read_outlet_states(self) -> Dict[int, bool]:
        """Read all outlet states via Telnet.

        Returns:
            Dict mapping outlet numbers to state (True=On, False=Off)
        """
        try:
            response = await self.send_telnet_command("show outlets all")

            outlet_states = {}

            # Parse response using the format from response_samples
            # Look for patterns like "Outlet 1 - Name:\nPower state: On"
            import re

            # Match outlet number and subsequent state
            pattern = r"Outlet (\d+)[^\n]*\n[^\n]*Power state:\s*(\w+)"
            matches = re.findall(pattern, response, re.IGNORECASE)

            for outlet_str, state_str in matches:
                outlet_num = int(outlet_str)
                state = state_str.lower() in ["on", "true", "1"]
                outlet_states[outlet_num] = state
                _LOGGER.debug("Telnet outlet %d state: %s", outlet_num, state)

            _LOGGER.info("Read %d outlet states via Telnet", len(outlet_states))
            return outlet_states

        except Exception as err:
            _LOGGER.error("Error reading Telnet outlet states: %s", err)
            return {}

    async def _handle_telnet_authentication(self) -> bool:
        """Handle Telnet authentication sequence.

        Returns:
            bool: True if authentication successful
        """
        if not (self.config.username and self.config.password):
            _LOGGER.error("Username and password required for Telnet authentication")
            return False

        try:
            # Use stored initial data from protocol detection
            initial_data = getattr(self, '_initial_data', b'')
            initial_text = initial_data.decode("utf-8", errors="ignore")
            _LOGGER.debug("Telnet initial response: %s", initial_text[:200])

            # Send username
            self._writer.write(f"{self.config.username}\r\n".encode())
            await self._writer.drain()

            # Read response (should ask for password)
            response = await asyncio.wait_for(self._reader.read(1024), timeout=5.0)
            response_text = response.decode("utf-8", errors="ignore")
            _LOGGER.debug("Username response: %s", response_text[:200])

            # Send password
            self._writer.write(f"{self.config.password}\r\n".encode())
            await self._writer.drain()

            # Read final authentication response
            auth_response = await asyncio.wait_for(self._reader.read(1024), timeout=5.0)
            auth_text = auth_response.decode("utf-8", errors="ignore")
            _LOGGER.debug("Password response: %s", auth_text[:200])

            # Check for successful login (welcome message or command prompt)
            if any(
                indicator in auth_text.lower()
                for indicator in ["welcome", "last login", "] # ", "> ", "$ "]
            ):
                _LOGGER.info("Telnet authentication successful")
                self._authenticated = True
                return True
            else:
                _LOGGER.error("Telnet authentication failed")
                return False

        except asyncio.TimeoutError:
            _LOGGER.error("Timeout during Telnet authentication")
            return False
        except Exception as err:
            _LOGGER.error("Telnet authentication error: %s", err)
            return False

    # Legacy method for compatibility with existing controller code
    async def send_command(self, command: str) -> str:
        """Legacy text command method - now routes to appropriate protocol.

        This method provides compatibility with the existing controller code
        that expects text-based commands.
        """
        _LOGGER.debug("Legacy command called: %s", command)

        # Route to appropriate protocol based on connection type
        # For now, try Telnet if we detect we're using a Telnet connection
        if hasattr(self, "_protocol_type") and self._protocol_type == "telnet":
            return await self.send_telnet_command(command)
        else:
            _LOGGER.warning(
                "Legacy command '%s' - no appropriate protocol handler", command
            )
            return ""
