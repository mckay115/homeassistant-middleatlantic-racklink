"""Config flow for Middle Atlantic RackLink."""

# Standard library imports
import asyncio
import logging
from typing import Any, Dict, List, Optional

# Third-party imports
import voluptuous as vol

# Home Assistant core imports
from homeassistant import config_entries
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import AbortFlow, FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

# Local application/library specific imports
from .const import (
    DOMAIN,
    DEFAULT_PORT,
    DEFAULT_REDFISH_PORT,
    DEFAULT_REDFISH_HTTP_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USERNAME,
    DEFAULT_PASSWORD,
    CONF_CONNECTION_TYPE,
    CONF_USE_HTTPS,
    CONF_ENABLE_VENDOR_FEATURES,
    CONNECTION_TYPE_REDFISH,
    CONNECTION_TYPE_TELNET,
    CONNECTION_TYPE_AUTO,
    CONNECTION_TYPE_DESCRIPTIONS,
)
from .controller.racklink_controller import RacklinkController
from .discovery import discover_racklink_devices, DiscoveredDevice

_LOGGER = logging.getLogger(__name__)

# Constants
CONNECTION_TIMEOUT = 10

# Data schema for connection type selection
STEP_CONNECTION_TYPE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CONNECTION_TYPE, default=CONNECTION_TYPE_AUTO): vol.In(
            CONNECTION_TYPE_DESCRIPTIONS
        ),
    }
)

# Data schema for the user input in the config flow
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): cv.string,
        vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=65535)
        ),
        vol.Optional(CONF_USE_HTTPS, default=True): cv.boolean,
    }
)

# Data schema for the options flow
OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
            vol.Coerce(int), vol.Range(min=5, max=300)
        ),
    }
)


async def validate_connection(
    _hass: HomeAssistant, data: Dict[str, Any]
) -> Dict[str, Any]:
    """Validate the connection to the RackLink PDU."""
    host = data[CONF_HOST]
    port = data[CONF_PORT]
    username = data[CONF_USERNAME]
    password = data[CONF_PASSWORD]
    connection_type = data.get(CONF_CONNECTION_TYPE, CONNECTION_TYPE_AUTO)
    use_https = data.get(CONF_USE_HTTPS, True)
    enable_vendor_features = data.get(CONF_ENABLE_VENDOR_FEATURES, True)

    controller = RacklinkController(
        host=host,
        port=port,
        username=username,
        password=password,
        timeout=CONNECTION_TIMEOUT,
        connection_type=connection_type,
        use_https=use_https,
        enable_vendor_features=enable_vendor_features,
    )

    try:
        # Connect to the device
        if not await controller.connect():
            _LOGGER.error("Failed to connect to device: %s:%s", host, port)
            raise CannotConnect("Connection failed")

        # Try to retrieve device information
        await controller.update()

        # Test basic device communication
        device_responsive = True
        try:
            if hasattr(controller.connection, "send_command"):
                # For socket connections, test with help command
                test_response = await controller.connection.send_command("help")
                _LOGGER.info(
                    "Device connectivity test - 'help' command response: %r",
                    test_response[:200],
                )
                device_responsive = bool(test_response.strip())
            elif hasattr(controller.connection, "get_outlet_count"):
                # For Redfish connections, test by getting outlet count
                outlet_count = controller.connection.get_outlet_count()
                device_responsive = outlet_count > 0
                _LOGGER.info(
                    "Device connectivity test - outlet count: %d", outlet_count
                )
        except Exception as err:
            _LOGGER.warning("Device connectivity test failed: %s", err)
            device_responsive = False

        # Validate that we can at least communicate with the device
        # Even if parsing fails, as long as we're connected, authenticated, and device responds, it's valid
        if (
            not controller.connection.connected
            or not controller.connection.authenticated
        ):
            _LOGGER.error("Failed to establish authenticated connection to device")
            await controller.disconnect()
            raise CannotConnect("Authentication failed")

        if not device_responsive:
            _LOGGER.warning(
                "Device not responding to basic commands, but connection established"
            )
            # Don't fail here - device might have different command format

        # Log what information we were able to retrieve
        _LOGGER.info(
            "Device validation: name=%s, model=%s, firmware=%s, serial=%s, mac=%s",
            controller.pdu_name or "Unknown",
            controller.pdu_model or "Unknown",
            controller.pdu_firmware or "Unknown",
            controller.pdu_serial or "Unknown",
            controller.mac_address or "Unknown",
        )

        # Get device information for the config entry title
        info = {
            "pdu_name": controller.pdu_name or "RackLink PDU",
            "pdu_model": controller.pdu_model or "Unknown Model",
            "pdu_firmware": controller.pdu_firmware or "Unknown Firmware",
            "pdu_serial": controller.pdu_serial or "Unknown Serial",
            "mac_address": controller.mac_address or "Unknown MAC",
        }

        _LOGGER.info(
            "Successfully connected to %s (%s)", info["pdu_name"], info["pdu_model"]
        )

        # Properly disconnect from the device
        await controller.disconnect()
        return info

    except asyncio.TimeoutError as exc:
        _LOGGER.error("Timeout connecting to device: %s:%s", host, port)
        await controller.disconnect()
        raise CannotConnect("Connection timeout") from exc
    except (OSError, asyncio.exceptions.CancelledError) as exc:
        _LOGGER.error("Error connecting to device: %s", exc)
        await controller.disconnect()
        raise CannotConnect(f"Connection error: {exc}") from exc
    except ValueError as exc:
        _LOGGER.error("Authentication failed: %s", exc)
        await controller.disconnect()
        raise InvalidAuth(f"Authentication failed: {exc}") from exc
    except Exception as exc:
        _LOGGER.error("Error connecting to device: %s", exc)
        await controller.disconnect()
        raise CannotConnect(f"Error connecting to device: {exc}") from exc


class MiddleAtlanticRacklinkConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Middle Atlantic RackLink."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_devices: List[DiscoveredDevice] = []
        self._discovery_completed = False
        self._connection_type: Optional[str] = (
            None  # Will trigger connection type selection
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "OptionsFlowHandler":
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)

    def is_matching(self, other_flow: config_entries.ConfigEntry) -> bool:
        """Check if the entry matches the current flow."""
        return other_flow.unique_id == self.unique_id

    async def async_step_zeroconf(self, discovery_info: Dict[str, Any]) -> FlowResult:
        """Handle zeroconf discovery."""
        _LOGGER.debug("Zeroconf discovery info: %s", discovery_info)

        # Extract device info from zeroconf discovery
        hostname = discovery_info.get("hostname", "").rstrip(".")
        properties = discovery_info.get("properties", {})

        # Check if this looks like a RackLink device
        if not any(
            identifier in hostname.lower()
            for identifier in ["racklink", "pdu", "power"]
        ):
            return self.async_abort(reason="not_racklink_device")

        # Set unique ID based on hostname
        await self.async_set_unique_id(hostname)
        self._abort_if_unique_id_configured()

        # Store discovery info and proceed to user confirmation
        self.context["title_placeholders"] = {"name": hostname}
        return await self.async_step_zeroconf_confirm(discovery_info)

    async def async_step_zeroconf_confirm(
        self, discovery_info: Dict[str, Any]
    ) -> FlowResult:
        """Confirm zeroconf discovery."""
        hostname = discovery_info.get("hostname", "").rstrip(".")
        host = discovery_info.get("host")
        port = discovery_info.get("port", DEFAULT_PORT)

        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        # Pre-fill the form with discovered information
        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=host): cv.string,
                vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): cv.string,
                vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): cv.string,
                vol.Optional(CONF_PORT, default=port): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
            }
        )

        return self.async_show_form(
            step_id="zeroconf_confirm",
            data_schema=data_schema,
            description_placeholders={"hostname": hostname},
        )

    async def async_step_connection_type(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle connection type selection."""
        if user_input is not None:
            self._connection_type = user_input[CONF_CONNECTION_TYPE]
            return await self.async_step_user()

        return self.async_show_form(
            step_id="connection_type",
            data_schema=STEP_CONNECTION_TYPE_SCHEMA,
        )

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: Dict[str, str] = {}

        # If no connection type selected yet, go to connection type selection
        if not hasattr(self, "_connection_type") or not self._connection_type:
            return await self.async_step_connection_type()

        # If no input yet, try to discover devices first
        if user_input is None and not self._discovery_completed:
            return await self.async_step_discovery()

        if user_input is not None:
            try:
                # Add connection type to user input
                user_input[CONF_CONNECTION_TYPE] = self._connection_type

                # Set default port based on connection type if not provided
                if CONF_PORT not in user_input or user_input[CONF_PORT] == DEFAULT_PORT:
                    if self._connection_type == CONNECTION_TYPE_REDFISH:
                        user_input[CONF_PORT] = (
                            DEFAULT_REDFISH_PORT
                            if user_input.get(CONF_USE_HTTPS, True)
                            else DEFAULT_REDFISH_HTTP_PORT
                        )
                    else:
                        user_input[CONF_PORT] = DEFAULT_PORT

                # If connection fails, try to discover the correct port
                if not await self._test_connection_with_discovery(user_input):
                    # Check if it was a protocol mismatch (port accessible but auth failed)
                    from .socket_connection import SocketConnection, SocketConfig

                    config = SocketConfig(
                        host=user_input[CONF_HOST],
                        port=user_input[CONF_PORT],
                        username=user_input[CONF_USERNAME],
                        password=user_input[CONF_PASSWORD],
                    )
                    socket_conn = SocketConnection(config)

                    # If port is accessible but auth failed, it's likely a protocol mismatch
                    if await socket_conn.test_port_connectivity(user_input[CONF_PORT]):
                        errors["base"] = "protocol_mismatch"
                    else:
                        errors["base"] = "cannot_connect_after_discovery"
                else:
                    info = await validate_connection(self.hass, user_input)

                    # Handle unique ID based on MAC address
                    mac_address = info.get("mac_address")
                    if mac_address and mac_address != "Unknown MAC":
                        # Set unique ID and check for duplicates
                        await self.async_set_unique_id(mac_address)
                        try:
                            self._abort_if_unique_id_configured(updates=user_input)
                        except AbortFlow as err:
                            return self.async_abort(reason=err.reason)

                    # Create a friendly title for the config entry
                    title = info["pdu_name"]
                    if info["pdu_model"] != "Unknown Model":
                        title = f"{title} ({info['pdu_model']})"

                    return self.async_create_entry(title=title, data=user_input)

            except CannotConnect as err:
                _LOGGER.warning("Cannot connect to device: %s", err)
                errors["base"] = "cannot_connect"
            except InvalidAuth as err:
                _LOGGER.warning("Invalid authentication: %s", err)
                errors["base"] = "invalid_auth"
            except ValueError as err:
                _LOGGER.warning("Invalid input: %s", err)
                errors["base"] = "invalid_input"
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during config flow: %s", err)
                errors["base"] = "unknown"

        # Show discovered devices or manual entry form
        if self._discovered_devices:
            return self.async_show_form(
                step_id="user",
                data_schema=self._build_device_selection_schema(),
                errors=errors,
                description_placeholders={
                    "discovered_count": str(len(self._discovered_devices))
                },
            )
        else:
            return self.async_show_form(
                step_id="user",
                data_schema=self._build_user_data_schema(),
                errors=errors,
            )

    async def async_step_discovery(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle device discovery."""
        _LOGGER.info("Starting RackLink device discovery...")

        try:
            # Discover devices using mDNS
            self._discovered_devices = await discover_racklink_devices(
                self.hass, timeout=8.0
            )
            self._discovery_completed = True

            _LOGGER.info("Discovery found %d devices", len(self._discovered_devices))

            if len(self._discovered_devices) == 1:
                # Auto-select the single device
                device = self._discovered_devices[0]
                return self.async_show_form(
                    step_id="user",
                    data_schema=self._build_single_device_schema(device),
                    description_placeholders={"device_name": device.name},
                )

        except Exception as err:
            _LOGGER.error("Error during discovery: %s", err)

        # Proceed to manual entry
        return await self.async_step_user()

    async def _test_connection_with_discovery(self, user_input: Dict[str, Any]) -> bool:
        """Test connection and try to discover correct port if needed."""
        from .socket_connection import SocketConnection, SocketConfig

        host = user_input[CONF_HOST]
        port = user_input[CONF_PORT]
        username = user_input[CONF_USERNAME]
        password = user_input[CONF_PASSWORD]

        # First, try the provided port with full authentication test
        config = SocketConfig(
            host=host, port=port, username=username, password=password
        )
        socket_conn = SocketConnection(config)

        try:
            # Test actual connection and authentication, not just port accessibility
            connection_result = await socket_conn.connect()
            if (
                connection_result
                and socket_conn.connected
                and socket_conn.authenticated
            ):
                _LOGGER.info("Successfully authenticated on port %d", port)
                await socket_conn.disconnect()
                return True
            elif socket_conn.connected and not socket_conn.authenticated:
                _LOGGER.warning("Port %d accessible but authentication failed", port)
                await socket_conn.disconnect()
            else:
                _LOGGER.warning("Failed to establish connection to port %d", port)
                if socket_conn.connected:
                    await socket_conn.disconnect()
        except Exception as err:
            _LOGGER.warning("Connection failed on port %d: %s", port, err)
            if socket_conn.connected:
                await socket_conn.disconnect()

        # If authentication fails, try to discover the correct port
        _LOGGER.info("Trying to discover correct port and protocol...")
        discovered_port = await socket_conn.discover_racklink_port()

        if discovered_port and discovered_port != port:
            _LOGGER.info("Discovered working port %d, updating config", discovered_port)
            user_input[CONF_PORT] = discovered_port

            # Test the discovered port
            try:
                config.port = discovered_port
                socket_conn = SocketConnection(config)
                connection_result = await socket_conn.connect()
                if (
                    connection_result
                    and socket_conn.connected
                    and socket_conn.authenticated
                ):
                    _LOGGER.info(
                        "Successfully authenticated on discovered port %d",
                        discovered_port,
                    )
                    await socket_conn.disconnect()
                    return True
                elif socket_conn.connected:
                    _LOGGER.warning(
                        "Connected to discovered port %d but authentication failed",
                        discovered_port,
                    )
                    await socket_conn.disconnect()
                else:
                    _LOGGER.warning(
                        "Failed to connect to discovered port %d",
                        discovered_port,
                    )
            except Exception as err:
                _LOGGER.error(
                    "Connection error on discovered port %d: %s",
                    discovered_port,
                    err,
                )

        return False

    def _build_user_data_schema(self) -> vol.Schema:
        """Build schema for manual entry based on connection type."""
        # Set default port based on connection type
        if self._connection_type == CONNECTION_TYPE_REDFISH:
            default_port = DEFAULT_REDFISH_PORT
            include_https = True
        else:
            default_port = DEFAULT_PORT
            include_https = False

        schema_dict = {
            vol.Required(CONF_HOST): cv.string,
            vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): cv.string,
            vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): cv.string,
            vol.Optional(CONF_PORT, default=default_port): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
        }

        if include_https:
            schema_dict[vol.Optional(CONF_USE_HTTPS, default=True)] = cv.boolean
            # For Redfish connections, offer vendor features option
            schema_dict[vol.Optional(CONF_ENABLE_VENDOR_FEATURES, default=True)] = (
                cv.boolean
            )

        return vol.Schema(schema_dict)

    def _build_single_device_schema(self, device: DiscoveredDevice) -> vol.Schema:
        """Build schema for a single discovered device."""
        # Determine port based on connection type
        if self._connection_type == CONNECTION_TYPE_REDFISH:
            default_port = DEFAULT_REDFISH_PORT
            include_https = True
        else:
            default_port = device.suggested_control_port
            include_https = False

        schema_dict = {
            vol.Required(CONF_HOST, default=device.ip_address): cv.string,
            vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): cv.string,
            vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): cv.string,
            vol.Optional(CONF_PORT, default=default_port): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
        }

        if include_https:
            schema_dict[vol.Optional(CONF_USE_HTTPS, default=True)] = cv.boolean
            # For Redfish connections, offer vendor features option
            schema_dict[vol.Optional(CONF_ENABLE_VENDOR_FEATURES, default=True)] = (
                cv.boolean
            )

        return vol.Schema(schema_dict)

    def _build_device_selection_schema(self) -> vol.Schema:
        """Build schema with discovered devices."""
        if not self._discovered_devices:
            return self._build_user_data_schema()

        # Create device options for selection
        device_options = {}
        for device in self._discovered_devices:
            key = f"{device.ip_address}:{device.suggested_control_port}"
            label = f"{device.name} ({device.ip_address})"
            device_options[key] = label

        # Add manual entry option
        device_options["manual"] = "Enter manually"

        schema_dict = {
            vol.Required("device"): vol.In(device_options),
            vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): cv.string,
            vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): cv.string,
        }

        # Add HTTPS option for Redfish
        if self._connection_type == CONNECTION_TYPE_REDFISH:
            schema_dict[vol.Optional(CONF_USE_HTTPS, default=True)] = cv.boolean
            # For Redfish connections, offer vendor features option
            schema_dict[vol.Optional(CONF_ENABLE_VENDOR_FEATURES, default=True)] = (
                cv.boolean
            )

        return vol.Schema(schema_dict)

    async def async_step_import(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle import from configuration.yaml."""
        return await self.async_step_user(user_input)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Middle Atlantic RackLink."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle options flow."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
            }
        )

        return self.async_show_form(step_id="init", data_schema=data_schema)


# Add back exception class definitions that were removed earlier
class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
