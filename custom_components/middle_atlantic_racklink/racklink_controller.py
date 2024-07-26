import asyncio
import telnetlib
import re
import logging

_LOGGER = logging.getLogger(__name__)

class RacklinkController:
    def __init__(self, host, port, username, password):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.telnet = None
        self.response_cache = ""
        self.last_cmd = ""
        self.context = ""
        self.pdu_name = ""
        self.pdu_firmware = ""
        self.pdu_serial = ""
        self.pdu_mac = ""
        self.outlet_states = {}
        self.outlet_names = {}
        self.outlet_power = {}
        self.outlet_current = {}
        self.sensors = {}

    async def connect(self):
        try:
            self.telnet = telnetlib.Telnet(self.host, self.port, timeout=10)
            await self.login()
            await self.get_initial_status()
        except Exception as e:
            _LOGGER.error(f"Connection failed: {e}")
            raise ValueError(f"Connection failed: {e}")

    async def reconnect(self):
        _LOGGER.info("Attempting to reconnect...")
        await self.disconnect()
        await self.connect()

    async def send_command(self, cmd):
        try:
            self.telnet.write(f"{cmd}\r\n".encode())
            self.last_cmd = cmd
            self.context = cmd.replace(" ", "")
            response = self.telnet.read_until(b"#", timeout=5).decode()
            self.response_cache = response
            return response
        except Exception as e:
            _LOGGER.error(f"Error sending command: {e}")
            await self.reconnect()
            return await self.send_command(cmd)

    async def login(self):
        self.telnet.read_until(b"Username:")
        self.telnet.write(f"{self.username}\r\n".encode())
        self.telnet.read_until(b"Password:")
        self.telnet.write(f"{self.password}\r\n".encode())
        response = self.telnet.read_until(b"#", timeout=5)
        if b"#" not in response:
            raise ValueError("Login failed: Invalid credentials")

    async def get_initial_status(self):
        await self.get_pdu_details()
        await self.get_all_outlet_states()
        await self.get_sensor_values()

    async def get_pdu_details(self):
        response = await self.send_command("show pdu details")
        try:
            self.pdu_name = re.search(r"'(.+)'", response).group(1)
            self.pdu_firmware = re.search(r"Firmware Version: (.+)", response).group(1).strip()
            self.pdu_serial = re.search(r"Serial Number: (.+)", response).group(1).strip()
        except AttributeError:
            _LOGGER.error("Failed to parse PDU details")

        response = await self.send_command("show network interface eth1")
        try:
            self.pdu_mac = re.search(r"MAC address: (.+)", response).group(1).strip()
        except AttributeError:
            _LOGGER.error("Failed to parse MAC address")

    async def get_all_outlet_states(self):
        response = await self.send_command("show outlets all details")
        pattern = r"Outlet (\d+):\r\n(.*?)Power state: (On|Off).*?RMS Current: (.+)A.*?Active Power: (.+)W"
        for match in re.finditer(pattern, response, re.DOTALL):
            outlet = int(match.group(1))
            name = match.group(2).strip()
            state = match.group(3) == "On"
            current = float(match.group(4))
            power = float(match.group(5))
            self.outlet_states[outlet] = state
            self.outlet_names[outlet] = name
            self.outlet_power[outlet] = power
            self.outlet_current[outlet] = current

    async def set_outlet_state(self, outlet, state):
        cmd = f"power outlets {outlet} {'on' if state else 'off'} /y"
        await self.send_command(cmd)
        self.outlet_states[outlet] = state

    async def cycle_outlet(self, outlet):
        cmd = f"power outlets {outlet} cycle /y"
        await self.send_command(cmd)

    async def set_outlet_name(self, outlet, name):
        cmd = f"config"
        await self.send_command(cmd)
        cmd = f"outlet {outlet} name \"{name}\""
        await self.send_command(cmd)
        cmd = f"apply"
        await self.send_command(cmd)
        self.outlet_names[outlet] = name

    async def set_all_outlets(self, state):
        cmd = f"power outlets all {'on' if state else 'off'} /y"
        await self.send_command(cmd)
        for outlet in self.outlet_states:
            self.outlet_states[outlet] = state

    async def cycle_all_outlets(self):
        cmd = "power outlets all cycle /y"
        await self.send_command(cmd)

    async def set_pdu_name(self, name):
        cmd = f"config"
        await self.send_command(cmd)
        cmd = f"pdu name \"{name}\""
        await self.send_command(cmd)
        cmd = f"apply"
        await self.send_command(cmd)
        self.pdu_name = name

    async def disconnect(self):
        if self.telnet:
            self.telnet.close()

    async def periodic_update(self):
        while True:
            try:
                await self.get_all_outlet_states()
                await self.get_sensor_values()
            except Exception as e:
                _LOGGER.error(f"Error during periodic update: {e}")
                await self.reconnect()
            await asyncio.sleep(60)  # Update every 60 seconds

    async def get_surge_protection_status(self):
        response = await self.send_command("show pdu details")
        match = re.search(r"Surge Protection: (\w+)", response)
        if match:
            return match.group(1) == "Active"
        return None
    
    async def get_sensor_values(self):
        response = await self.send_command("show inlets all details")
        try:
            self.sensors["voltage"] = float(re.search(r"RMS Voltage: (.+)V", response).group(1))
            self.sensors["current"] = float(re.search(r"RMS Current: (.+)A", response).group(1))
            self.sensors["power"] = float(re.search(r"Active Power: (.+)W", response).group(1))
            
            temp_match = re.search(r"Temperature: (.+)°C", response)
            if temp_match:
                self.sensors["temperature"] = float(temp_match.group(1))
            else:
                self.sensors["temperature"] = None
        except AttributeError:
            _LOGGER.error("Failed to parse sensor values")

    async def get_all_outlet_statuses(self):
        response = await self.send_command("show outlets all")
        statuses = {}
        for match in re.finditer(r"Outlet (\d+).*?Power State: (\w+)", response, re.DOTALL):
            outlet = int(match.group(1))
            state = match.group(2) == "On"
            statuses[outlet] = state
        return statuses