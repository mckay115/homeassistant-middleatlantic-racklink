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
DEFAULT_TIMEOUT = 15  # Increased from 10 to 15 seconds
DEFAULT_RECONNECT_INTERVAL = 60  # seconds
DEFAULT_TELNET_TIMEOUT = 10  # Increased from 5 to 10 seconds

# Connection parameters
MAX_RECONNECT_ATTEMPTS = 3
CONNECTION_TIMEOUT = 20  # Increased from 15 to 20 seconds
COMMAND_TIMEOUT = 15  # Increased from 10 to 15 seconds

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
    "RLNK-P915R": "RackLink P915R (8 outlets, 15A)",
    "RLNK-P915R-SP": "RackLink P915R-SP (8 outlets, 15A, Surge Protected)",
    "RLNK-P920R": "RackLink P920R (8 outlets, 20A)",
    "RLNK-P920R-SP": "RackLink P920R-SP (8 outlets, 20A, Surge Protected)",
    "AUTO_DETECT": "Auto-detect model (recommended)",
}

# Supported platforms
PLATFORMS = [Platform.SWITCH, Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]

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

# Sensor value dict keys
SENSOR_PDU_TEMPERATURE = "temperature"
SENSOR_PDU_CURRENT = "current"
SENSOR_PDU_VOLTAGE = "voltage"
SENSOR_PDU_POWER = "power"
SENSOR_PDU_ENERGY = "energy"
SENSOR_PDU_POWER_FACTOR = "power_factor"
SENSOR_PDU_FREQUENCY = "frequency"

# Outlet power metrics
OUTLET_METRIC_CURRENT = "current"
OUTLET_METRIC_VOLTAGE = "voltage"
OUTLET_METRIC_POWER = "power"
OUTLET_METRIC_ENERGY = "energy"
OUTLET_METRIC_POWER_FACTOR = "power_factor"
OUTLET_METRIC_FREQUENCY = "frequency"
OUTLET_METRIC_APPARENT_POWER = "apparent_power"

# Units of measurement
UNIT_AMPERE = "A"
UNIT_VOLT = "V"
UNIT_WATT = "W"
UNIT_VA = "VA"
UNIT_WATTHOUR = "Wh"
UNIT_HERTZ = "Hz"
UNIT_CELSIUS = "°C"
UNIT_FAHRENHEIT = "°F"

# Command collection settings
MAX_OUTLETS_TO_UPDATE_PER_CYCLE = (
    3  # Number of outlets to collect detailed power data at once
)
UPDATE_CYCLE_DELAY = 0.5  # Seconds to delay between commands

# Optional settings
CONF_COLLECT_POWER_DATA = "collect_power_data"
