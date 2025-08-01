"""Constants for the Middle Atlantic RackLink integration."""

# from datetime import timedelta # Unused
# from homeassistant.const import Platform # Unused

# Integration domain
DOMAIN = "middle_atlantic_racklink"

# Default values
DEFAULT_PORT = 60000  # TCP port for RackLink Select, Premium, Premium+ series
DEFAULT_NAME = "Middle Atlantic Racklink"
DEFAULT_SCAN_INTERVAL = 30  # seconds - conservative to prevent session corruption
DEFAULT_TIMEOUT = 20
DEFAULT_RECONNECT_INTERVAL = 60  # seconds
DEFAULT_TELNET_TIMEOUT = 10  # Increased from 5 to 10 seconds
DEFAULT_USERNAME = "admin"  # Common default for RackLink devices
DEFAULT_PASSWORD = "password"  # Default password (often changed by users)

# Connection parameters
MAX_RECONNECT_ATTEMPTS = 3
CONNECTION_TIMEOUT = 20  # Increased from 15 to 20 seconds
COMMAND_TIMEOUT = 15  # Increased from 10 to 15 seconds

# Configuration options
CONF_SCAN_INTERVAL = "scan_interval"
CONF_DEVICE_ID = "device_id"
CONF_MODEL = "model"
CONF_PDU_NAME = "pdu_name"
CONF_PASSWORD = "password"

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
PLATFORMS = ["switch", "sensor"]

# Services
SERVICE_CYCLE_ALL_OUTLETS = "cycle_all_outlets"
SERVICE_CYCLE_OUTLET = "cycle_outlet"
SERVICE_SET_OUTLET_NAME = "set_outlet_name"
SERVICE_SET_PDU_NAME = "set_pdu_name"

# Attributes
ATTR_MANUFACTURER = "Legrand - Middle Atlantic"
ATTR_MODEL = "RackLink PDU"
ATTR_SERIAL_NUMBER = "serial_number"
ATTR_FIRMWARE_VERSION = "firmware_version"
ATTR_OUTLET_NUMBER = "outlet"
ATTR_POWER = "power"
ATTR_CURRENT = "current"
ATTR_ENERGY = "energy"
ATTR_TEMPERATURE = "temperature"
ATTR_POWER_FACTOR = "power_factor"
ATTR_FREQUENCY = "frequency"
ATTR_VOLTAGE = "voltage"
ATTR_OUTLET_NAME = "name"
ATTR_PDU_NAME = "name"

# Sensor value dict keys
SENSOR_PDU_TEMPERATURE = "pdu_temperature"
SENSOR_PDU_CURRENT = "pdu_current"
SENSOR_PDU_VOLTAGE = "pdu_voltage"
SENSOR_PDU_POWER = "pdu_power"
SENSOR_PDU_ENERGY = "pdu_energy"
SENSOR_PDU_POWER_FACTOR = "pdu_power_factor"
SENSOR_PDU_FREQUENCY = "pdu_frequency"

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

# PDU types
DEVICE_TYPES = {
    "RLNK-SW215": {"outlets": 2, "advanced": False},
    "RLNK-SW415": {"outlets": 4, "advanced": False},
    "RLNK-SW815": {"outlets": 8, "advanced": False},
    "RLNK-SW915": {"outlets": 9, "advanced": False},
    "RLNK-SW1115": {"outlets": 11, "advanced": False},
    "RLNK-215": {"outlets": 2, "advanced": False},
    "RLNK-415": {"outlets": 4, "advanced": False},
    "RLNK-415-20": {"outlets": 4, "advanced": True},
    "RLNK-815": {"outlets": 8, "advanced": False},
    "RLNK-820": {"outlets": 8, "advanced": True},
    "RLNK-915": {"outlets": 9, "advanced": False},
    "RLNK-920": {"outlets": 9, "advanced": True},
    "RLNK-1115": {"outlets": 11, "advanced": False},
    "RLNK-1120": {"outlets": 11, "advanced": True},
}

# Command query delay
COMMAND_QUERY_DELAY = 0.5  # seconds between commands

# Command discovery attempts
COMMAND_DISCOVERY_ATTEMPTS = 3

# Maximum failed commands
MAX_FAILED_COMMANDS = 5

# Maximum connection attempts
MAX_CONNECTION_ATTEMPTS = 3

# Maximum initial connection attempts
MAX_INITIAL_CONNECTION_ATTEMPTS = 5

# Device key for storing controller and coordinator
DATA_CONTROLLER = "controller"
DATA_COORDINATOR = "coordinator"
