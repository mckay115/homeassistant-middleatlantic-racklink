import asyncio
import logging
import re

# Configuration
PORT = 6000

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class RacklinkController:
    def __init__(self, ip, username, password, model):
        self.ip = ip
        self.username = username
        self.password = password
        self.model = model
        self.reader = None
        self.writer = None
        self.telnet_state = "SETUP"
        self.outlets = 8 if model.find("P9") != -1 else 4

        # PDU status
        self.pdu_name = ""
        self.pdu_firmware = ""
        self.pdu_serial = ""
        self.pdu_mac = ""
        self.pdu_current = 0.0
        self.pdu_voltage = 0
        self.pdu_power = 0
        self.outlet_states = [False] * self.outlets
        self.outlet_names = [""] * self.outlets
        self.outlet_currents = ["0.0A"] * self.outlets
        self.outlet_powers = ["0W"] * self.outlets
        self.outlet_energies = ["0Wh"] * self.outlets

    async def connect(self):
        logger.info(f"Connecting to {self.ip}:{PORT}")
        try:
            self.reader, self.writer = await asyncio.open_connection(self.ip, PORT)
            logger.info("Connected successfully")
            await self.telnet_auth()
        except Exception as e:
            logger.error(f"Connection failed: {e}")

    async def telnet_auth(self):
        try:
            await self.reader.readuntil(b"Username:")
            self.writer.write(f"{self.username}\r\n".encode())
            await self.writer.drain()

            await self.reader.readuntil(b"Password:")
            self.writer.write(f"{self.password}\r\n".encode())
            await self.writer.drain()

            response = await self.reader.readuntil(b"#")
            if b"#" in response:
                logger.info("Authentication successful")
                await self.get_status()
            else:
                logger.error("Authentication failed")
        except Exception as e:
            logger.error(f"Authentication failed: {e}")

    async def get_status(self):
        status_commands = ["show pdu details", "show outlets all details", "show inlets all details"]
        for cmd in status_commands:
            await self.send_command(cmd)

    async def send_command(self, cmd):
        logger.debug(f"Sending: {cmd}")
        self.writer.write(f"{cmd}\r\n".encode())
        await self.writer.drain()
        response = await self.read_response()
        self.parse_response(cmd, response)

    async def read_response(self):
        response = await self.reader.readuntil(b"#")
        return response.decode()

    def parse_response(self, cmd, response):
        if "show pdu details" in cmd:
            self.parse_pdu_details(response)
        elif "show outlets all details" in cmd:
            self.parse_outlets_details(response)
        elif "show inlets all details" in cmd:
            self.parse_inlet_details(response)

    def parse_pdu_details(self, data):
        self.pdu_name = re.search("'(.+)'", data).group(1)
        self.pdu_firmware = re.search("Firmware Version: (.+)\r\n", data).group(1).strip()
        self.pdu_serial = re.search("Serial Number: (.+)\r\n", data).group(1).strip()

    def parse_outlets_details(self, data):
        outlets = re.findall(r"Outlet (\d.+):\r\n(P.+)V\r\n\r\n", data)
        for outlet in outlets:
            index = int(re.search(r"(\d)", outlet[0]).group(1)) - 1
            self.outlet_states[index] = "Power state: On" in outlet[1]
            self.outlet_names[index] = re.sub(r"\d\s*-\s*", "", outlet[0])
            self.outlet_currents[index] = re.search(r"RMS Current: (.+A)", outlet[1]).group(1).strip()
            self.outlet_powers[index] = re.search(r"Active Power: (.+W)", outlet[1]).group(1).strip()
            self.outlet_energies[index] = re.search(r"Active Energy: (.+Wh)", outlet[1]).group(1).strip()

    def parse_inlet_details(self, data):
        self.pdu_voltage = int(re.search(r"RMS Voltage:(.+)V", data).group(1))
        self.pdu_power = int(re.search(r"Active Power:(.+)W", data).group(1))
        self.pdu_current = float(re.search(r"RMS Current:(.+)A", data).group(1))

    def print_status(self):
        print("\n--- PDU Status ---")
        print(f"Name: {self.pdu_name}")
        print(f"Firmware: {self.pdu_firmware}")
        print(f"Serial Number: {self.pdu_serial}")
        print(f"Voltage: {self.pdu_voltage}V")
        print(f"Current: {self.pdu_current}A")
        print(f"Power: {self.pdu_power}W")
        
        print("\n--- Outlet Status ---")
        for i in range(self.outlets):
            print(f"Outlet {i+1}: {self.outlet_names[i]}")
            print(f"  State: {'ON' if self.outlet_states[i] else 'OFF'}")
            print(f"  Current: {self.outlet_currents[i]}")
            print(f"  Power: {self.outlet_powers[i]}")
            print(f"  Energy: {self.outlet_energies[i]}")
            print()

async def main():
    controller = RacklinkController("10.0.1.211", "ha", "slot6.wrk", "RLNK-P920R")
    await controller.connect()
    await asyncio.sleep(2)  # Give some time for all status commands to complete
    controller.print_status()

    if controller.writer:
        controller.writer.close()
        await controller.writer.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())