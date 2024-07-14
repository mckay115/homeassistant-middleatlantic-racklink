import asyncio
import logging
import struct

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
        self.outlet_states = {}
        self.sensors = {}

    async def connect(self):
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        await self.login()
        asyncio.create_task(self.listen_for_updates())

    async def disconnect(self):
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()

    async def login(self):
        login_msg = self.create_message(0x02, 0x01, f"{self.username}|{self.password}".encode())
        await self.send_message(login_msg)
        response = await self.read_message()
        # Handle login response

    async def send_message(self, message):
        self.writer.write(message)
        await self.writer.drain()

    async def read_message(self):
        header = await self.reader.readexactly(1)
        if header != b'\xfe':
            raise ValueError("Invalid header")
        
        length = await self.reader.readexactly(1)
        length = struct.unpack('B', length)[0]
        
        data = await self.reader.readexactly(length)
        
        checksum = await self.reader.readexactly(1)
        tail = await self.reader.readexactly(1)
        
        if tail != b'\xff':
            raise ValueError("Invalid tail")
        
        return data

    def create_message(self, command, subcommand, data=b''):
        message = struct.pack('BBB', 0xfe, len(data) + 3, 0x00)
        message += struct.pack('BB', command, subcommand)
        message += data
        checksum = sum(message[1:]) & 0x7f
        message += struct.pack('BB', checksum, 0xff)
        return message

    async def get_outlet_state(self, outlet):
        message = self.create_message(0x20, 0x02, struct.pack('B', outlet))
        await self.send_message(message)
        response = await self.read_message()
        # Parse response and update outlet_states

    async def set_outlet_state(self, outlet, state):
        message = self.create_message(0x20, 0x01, struct.pack('BB', outlet, state))
        await self.send_message(message)
        response = await self.read_message()
        # Parse response and update outlet_states

    async def get_sensor_value(self, sensor_type):
        message = self.create_message(sensor_type, 0x02)
        await self.send_message(message)
        response = await self.read_message()
        # Parse response and update sensors

    async def listen_for_updates(self):
        while True:
            try:
                message = await self.read_message()
                # Parse message and update relevant states
                # Dispatch state change events
            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.error(f"Error reading message: {e}")
                await asyncio.sleep(5)