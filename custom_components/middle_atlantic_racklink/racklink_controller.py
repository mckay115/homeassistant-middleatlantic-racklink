import asyncio
import logging
import struct
from typing import Dict, Any

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
        self.sensors: Dict[int, Any] = {}
        self._lock = asyncio.Lock()

    async def connect(self):
        try:
            self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
            await self.login()
            self.hass.loop.create_task(self.listen_for_updates())
        except Exception as e:
            _LOGGER.error(f"Failed to connect: {e}")
            raise

    async def disconnect(self):
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()

    async def login(self):
        login_msg = self._create_message(0x02, 0x01, f"{self.username}|{self.password}".encode())
        response = await self._send_and_receive(login_msg)
        if response[2] != 0x01:
            raise ValueError("Login failed")

    async def _send_and_receive(self, message):
        async with self._lock:
            self.writer.write(message)
            await self.writer.drain()
            return await self._read_message()

    async def _read_message(self):
        header = await self.reader.readexactly(1)
        if header != b'\xfe':
            raise ValueError("Invalid header")
        
        length = struct.unpack('B', await self.reader.readexactly(1))[0]
        data = await self.reader.readexactly(length)
        
        checksum = await self.reader.readexactly(1)
        tail = await self.reader.readexactly(1)
        
        if tail != b'\xff':
            raise ValueError("Invalid tail")
        
        return data

    def _create_message(self, command, subcommand, data=b''):
        message = struct.pack('BBB', 0xfe, len(data) + 3, 0x00)
        message += struct.pack('BB', command, subcommand)
        message += data
        checksum = sum(message[1:]) & 0x7f
        message += struct.pack('BB', checksum, 0xff)
        return message

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
        if sensor_type in [0x51, 0x52, 0x55]:  # Voltage, Temperature
            return int(data.decode())
        elif sensor_type in [0x53, 0x54, 0x57]:  # Current, Power Factor
            return float(data.decode())
        elif sensor_type == 0x56:  # Wattage
            return int(data.decode())
        elif sensor_type == 0x59:  # Surge Protection
            return data[0] == 0x01
        else:
            return data.decode()

    async def sequence_power_outlets(self, direction, delay):
        message = self._create_message(0x36, 0x01, struct.pack('B', direction) + delay.encode())
        await self._send_and_receive(message)

    async def set_epo_state(self, state):
        message = self._create_message(0x37, 0x01, struct.pack('B', 0x01 if state else 0x00))
        await self._send_and_receive(message)

    async def get_product_info(self, info_type):
        message = self._create_message(info_type, 0x02)
        response = await self._send_and_receive(message)
        return response[3:].decode().strip()

    async def listen_for_updates(self):
        while True:
            try:
                message = await self._read_message()
                command = message[1]
                subcommand = message[2]
                
                if command == 0x20 and subcommand == 0x12:  # Outlet status change
                    outlet = message[3]
                    state = message[4] == 0x01
                    self.outlet_states[outlet] = state
                    self.hass.bus.async_fire("racklink_outlet_state_changed", {"outlet": outlet, "state": state})
                
                elif command in [0x50, 0x51, 0x52, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59]:  # Sensor updates
                    value = self._parse_sensor_value(command, message[3:])
                    self.sensors[command] = value
                    self.hass.bus.async_fire("racklink_sensor_updated", {"sensor_type": command, "value": value})
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.error(f"Error reading message: {e}")
                await asyncio.sleep(5)