import asyncio
import logging
import struct
from typing import Dict, Any
from asyncio import TimeoutError

_LOGGER = logging.getLogger(__name__)

class RacklinkController:
    def __init__(self, hass, host, port, username, password):
        self.hass = hass
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.reader = None
        self.writer = None
        self.outlet_states: Dict[int, bool] = {}
        self.outlet_names: Dict[int, str] = {}
        self.sensors: Dict[int, Any] = {}
        self._lock = asyncio.Lock()
        self.pdu_name = ""
        self.pdu_firmware = ""
        self.pdu_serial = ""
        self.pdu_mac = ""

    async def connect(self):
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=10
            )
            await self.login()
            await self.get_initial_status()
            self.hass.loop.create_task(self.listen_for_updates())
        except (OSError, TimeoutError, ValueError) as e:
            _LOGGER.error(f"Failed to connect: {e}")
            if self.writer:
                self.writer.close()
                await self.writer.wait_closed()
            raise ValueError(f"Connection failed: {e}")

    async def disconnect(self):
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()

    async def login(self):
        login_msg = self._create_message(0x02, 0x01, f"{self.username}|{self.password}".encode())
        try:
            response = await asyncio.wait_for(self._send_and_receive(login_msg), timeout=10)
            if response[2] != 0x01:
                raise ValueError("Login failed: Invalid credentials")
        except TimeoutError:
            raise ValueError("Login failed: Timeout")
        except Exception as e:
            raise ValueError(f"Login failed: {e}")

    async def get_initial_status(self):
        await self.get_pdu_details()
        await self.get_all_outlet_states()
        await self.get_sensor_values()

    async def get_pdu_details(self):
        message = self._create_message(0x90, 0x02)
        response = await self._send_and_receive(message)
        self.pdu_name = response[3:].decode().strip()

        message = self._create_message(0x91, 0x02)
        response = await self._send_and_receive(message)
        self.pdu_firmware = response[3:].decode().strip()

        message = self._create_message(0x95, 0x02)
        response = await self._send_and_receive(message)
        self.pdu_mac = response[3:].decode().strip()

    async def get_all_outlet_states(self):
        for outlet in range(1, 17):
            await self.get_outlet_state(outlet)

    async def get_sensor_values(self):
        for sensor_type in [0x52, 0x54, 0x56, 0x55, 0x59]:
            await self.get_sensor_value(sensor_type)

    async def get_outlet_state(self, outlet):
        message = self._create_message(0x20, 0x02, struct.pack('B', outlet))
        response = await self._send_and_receive(message)
        state = response[3] == 0x01
        self.outlet_states[outlet] = state
        return state

    async def set_outlet_state(self, outlet, state):
        message = self._create_message(0x20, 0x01, struct.pack('BB', outlet, 0x01 if state else 0x00))
        await self._send_and_receive(message)
        self.outlet_states[outlet] = state
        return state

    async def get_sensor_value(self, sensor_type):
        message = self._create_message(sensor_type, 0x02)
        response = await self._send_and_receive(message)
        value = self._parse_sensor_value(sensor_type, response[3:])
        self.sensors[sensor_type] = value
        return value

    def _parse_sensor_value(self, sensor_type, data):
        if sensor_type in [0x51, 0x52, 0x55]:
            return int(data.decode())
        elif sensor_type in [0x53, 0x54, 0x57]:
            return float(data.decode())
        elif sensor_type == 0x56:
            return int(data.decode())
        elif sensor_type == 0x59:
            return data[0] == 0x01
        else:
            return data.decode()

    async def set_outlet_name(self, outlet, name):
        message = self._create_message(0x21, 0x01, struct.pack('B', outlet) + name.encode())
        await self._send_and_receive(message)
        self.outlet_names[outlet] = name

    async def get_outlet_name(self, outlet):
        message = self._create_message(0x21, 0x02, struct.pack('B', outlet))
        response = await self._send_and_receive(message)
        name = response[3:].decode().strip()
        self.outlet_names[outlet] = name
        return name

    async def cycle_outlet(self, outlet):
        message = self._create_message(0x20, 0x01, struct.pack('BB', outlet, 0x02))
        await self._send_and_receive(message)

    async def set_all_outlets(self, state):
        message = self._create_message(0x36, 0x01, struct.pack('B', 0x01 if state else 0x03))
        await self._send_and_receive(message)

    async def cycle_all_outlets(self):
        message = self._create_message(0x36, 0x01, struct.pack('B', 0x02))
        await self._send_and_receive(message)

    def _create_message(self, command, subcommand, data=b''):
        message = struct.pack('BBB', 0xfe, len(data) + 3, 0x00)
        message += struct.pack('BB', command, subcommand)
        message += data
        checksum = sum(message[1:]) & 0x7f
        message += struct.pack('BB', checksum, 0xff)
        return message

    async def _send_and_receive(self, message):
        async with self._lock:
            self.writer.write(message)
            await self.writer.drain()
            return await self._read_message()

    async def _read_message(self):
        try:
            header = await asyncio.wait_for(self.reader.readexactly(1), timeout=5)
            if header != b'\xfe':
                raise ValueError(f"Invalid header: {header.hex()}")
            
            length_byte = await asyncio.wait_for(self.reader.readexactly(1), timeout=5)
            length = struct.unpack('B', length_byte)[0]
            
            data = await asyncio.wait_for(self.reader.readexactly(length), timeout=5)
            
            checksum = await asyncio.wait_for(self.reader.readexactly(1), timeout=5)
            tail = await asyncio.wait_for(self.reader.readexactly(1), timeout=5)
            
            if tail != b'\xff':
                raise ValueError(f"Invalid tail: {tail.hex()}")
            
            return data
        except TimeoutError:
            raise ValueError("Timeout while reading message")
        except Exception as e:
            raise ValueError(f"Error reading message: {e}")

    async def listen_for_updates(self):
        while True:
            try:
                message = await self._read_message()
                command = message[1]
                subcommand = message[2]
                
                if command == 0x20 and subcommand == 0x12:
                    outlet = message[3]
                    state = message[4] == 0x01
                    self.outlet_states[outlet] = state
                    self.hass.bus.async_fire("racklink_outlet_state_changed", {"outlet": outlet, "state": state})
                
                elif command in [0x50, 0x51, 0x52, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59]:
                    value = self._parse_sensor_value(command, message[3:])
                    self.sensors[command] = value
                    self.hass.bus.async_fire("racklink_sensor_updated", {"sensor_type": command, "value": value})
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.error(f"Error reading message: {e}")
                await asyncio.sleep(5)