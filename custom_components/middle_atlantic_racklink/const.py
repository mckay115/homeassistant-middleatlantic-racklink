"""Constants for the Middle Atlantic RackLink integration."""

# Integration domain
DOMAIN = "middle_atlantic_racklink"

# Default values
DEFAULT_PORT = 6000  # TCP port for RackLink devices (most common)
DEFAULT_REDFISH_PORT = 443  # HTTPS port for Redfish API
DEFAULT_REDFISH_HTTP_PORT = 80  # HTTP port for Redfish API (fallback)
DEFAULT_NAME = "Middle Atlantic RackLink"

# Update intervals by connection type
DEFAULT_SCAN_INTERVAL_TELNET = (
    60  # Conservative for telnet to prevent session corruption
)
DEFAULT_SCAN_INTERVAL_REDFISH = 10  # Fast updates for REST API
DEFAULT_SCAN_INTERVAL = DEFAULT_SCAN_INTERVAL_TELNET  # Default fallback
DEFAULT_TIMEOUT = 20
DEFAULT_TELNET_TIMEOUT = 10
DEFAULT_USERNAME = "admin"  # Common default for RackLink devices

# Connection parameters
CONNECTION_TIMEOUT = 20
COMMAND_TIMEOUT = 15

# Configuration options
CONF_SCAN_INTERVAL = "scan_interval"
CONF_CONNECTION_TYPE = "connection_type"
CONF_USE_HTTPS = "use_https"
CONF_ENABLE_VENDOR_FEATURES = "enable_vendor_features"

# Connection types
CONNECTION_TYPE_REDFISH = "redfish"
CONNECTION_TYPE_TELNET = "telnet"
CONNECTION_TYPE_AUTO = "auto"

# Attributes
ATTR_MANUFACTURER = "Legrand - Middle Atlantic"
ATTR_MODEL = "RackLink PDU"

# Services
SERVICE_CYCLE_ALL_OUTLETS = "cycle_all_outlets"
SERVICE_CYCLE_OUTLET = "cycle_outlet"
SERVICE_SET_OUTLET_NAME = "set_outlet_name"
SERVICE_SET_PDU_NAME = "set_pdu_name"
