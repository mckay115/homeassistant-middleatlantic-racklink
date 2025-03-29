"""Constants for the Middle Atlantic Racklink integration."""

from datetime import timedelta

from homeassistant.const import Platform

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
CONF_MODEL = "model"

# Supported models
SUPPORTED_MODELS = [
    "RLNK-P415",
    "RLNK-P420",
    "RLNK-P915R",
    "RLNK-P915R-SP",
    "RLNK-P920R",
    "RLNK-P920R-SP",
    "AUTO_DETECT",  # Auto-detect model option
]

# Model descriptions for user-friendly display
MODEL_DESCRIPTIONS = {
    "RLNK-P415": "RackLink P415 (4 outlets, 15A)",
    "RLNK-P420": "RackLink P420 (4 outlets, 20A)",
    "RLNK-P915R": "RackLink P915R (9 outlets, 15A)",
    "RLNK-P915R-SP": "RackLink P915R-SP (9 outlets, 15A, Surge Protected)",
    "RLNK-P920R": "RackLink P920R (9 outlets, 20A)",
    "RLNK-P920R-SP": "RackLink P920R-SP (9 outlets, 20A, Surge Protected)",
    "AUTO_DETECT": "Auto-detect model (recommended)",
}

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
