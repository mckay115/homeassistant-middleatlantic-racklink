"""Constants for the Middle Atlantic Racklink integration."""

from datetime import timedelta

from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    Platform,
)

# Integration domain
DOMAIN = "middle_atlantic_racklink"

# Default values
DEFAULT_PORT = 6000
DEFAULT_NAME = "Middle Atlantic Racklink"
DEFAULT_SCAN_INTERVAL = timedelta(
    seconds=30
)  # Reduced frequency to prevent overloading
DEFAULT_TIMEOUT = 10
DEFAULT_RECONNECT_INTERVAL = 60  # seconds
DEFAULT_TELNET_TIMEOUT = 5  # seconds

# Connection parameters
MAX_RECONNECT_ATTEMPTS = 3
CONNECTION_TIMEOUT = 15  # seconds
COMMAND_TIMEOUT = 10  # seconds

# Configuration options
CONF_SCAN_INTERVAL = "scan_interval"
CONF_DEVICE_ID = "device_id"

# Supported platforms
PLATFORMS = [Platform.SWITCH, Platform.SENSOR, Platform.BINARY_SENSOR]

# Services
SERVICE_CYCLE_ALL_OUTLETS = "cycle_all_outlets"
SERVICE_CYCLE_OUTLET = "cycle_outlet"
SERVICE_SET_OUTLET_NAME = "set_outlet_name"
SERVICE_SET_PDU_NAME = "set_pdu_name"

# Attributes
ATTR_MANUFACTURER = "Middle Atlantic"
ATTR_MODEL = "Racklink PDU"
ATTR_SERIAL_NUMBER = "serial_number"
ATTR_FIRMWARE_VERSION = "firmware_version"
ATTR_OUTLET_NUMBER = "outlet_number"
ATTR_POWER = "power"
ATTR_CURRENT = "current"
ATTR_ENERGY = "energy"
ATTR_TEMPERATURE = "temperature"
ATTR_POWER_FACTOR = "power_factor"
ATTR_FREQUENCY = "frequency"
ATTR_VOLTAGE = "voltage"
