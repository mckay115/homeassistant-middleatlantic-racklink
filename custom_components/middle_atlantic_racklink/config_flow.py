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

# Data schema for connection type selection (no auto-detection)
STEP_CONNECTION_TYPE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CONNECTION_TYPE, default=CONNECTION_TYPE_REDFISH): vol.In(
            {
                CONNECTION_TYPE_REDFISH: CONNECTION_TYPE_DESCRIPTIONS[
                    CONNECTION_TYPE_REDFISH
                ],
                CONNECTION_TYPE_TELNET: CONNECTION_TYPE_DESCRIPTIONS[
                    CONNECTION_TYPE_TELNET
                ],
            }
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
        self._connection_type: Optional[str] = None

        # For storing selected device info
        self._selected_host: Optional[str] = None
        self._selected_port: Optional[int] = None
        self._selected_username: Optional[str] = None
        self._selected_password: Optional[str] = None

        # For storing manual input
        self._manual_input: Optional[Dict[str, Any]] = None

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

            # Build final config data
            if hasattr(self, "_selected_host"):
                # Using discovered device
                final_input = {
                    CONF_HOST: self._selected_host,
                    CONF_PORT: self._selected_port,
                    CONF_USERNAME: self._selected_username,
                    CONF_PASSWORD: self._selected_password,
                    CONF_CONNECTION_TYPE: self._connection_type,
                }
            elif hasattr(self, "_manual_input"):
                # Using manual input
                final_input = self._manual_input.copy()
                final_input[CONF_CONNECTION_TYPE] = self._connection_type
            else:
                # Fallback
                return await self.async_step_user()

            # Set default port based on connection type if not specified
            if CONF_PORT not in final_input or final_input[CONF_PORT] == DEFAULT_PORT:
                if self._connection_type == CONNECTION_TYPE_REDFISH:
                    final_input[CONF_PORT] = (
                        DEFAULT_REDFISH_PORT
                        if final_input.get(CONF_USE_HTTPS, True)
                        else DEFAULT_REDFISH_HTTP_PORT
                    )
                else:
                    final_input[CONF_PORT] = DEFAULT_PORT

            # Validate and create entry
            try:
                info = await validate_connection(self.hass, final_input)

                # Handle unique ID based on MAC address
                mac_address = info.get("mac_address")
                if mac_address and mac_address != "Unknown MAC":
                    await self.async_set_unique_id(mac_address)
                    try:
                        self._abort_if_unique_id_configured(updates=final_input)
                    except AbortFlow as err:
                        return self.async_abort(reason=err.reason)

                # Create entry
                title = info["pdu_name"]
                if info["pdu_model"] != "Unknown Model":
                    title = f"{title} ({info['pdu_model']})"

                return self.async_create_entry(title=title, data=final_input)

            except CannotConnect as err:
                return self.async_show_form(
                    step_id="connection_type",
                    data_schema=STEP_CONNECTION_TYPE_SCHEMA,
                    errors={"base": "cannot_connect"},
                )
            except InvalidAuth as err:
                return self.async_show_form(
                    step_id="connection_type",
                    data_schema=STEP_CONNECTION_TYPE_SCHEMA,
                    errors={"base": "invalid_auth"},
                )
            except Exception as err:
                return self.async_show_form(
                    step_id="connection_type",
                    data_schema=STEP_CONNECTION_TYPE_SCHEMA,
                    errors={"base": "unknown"},
                )

        return self.async_show_form(
            step_id="connection_type",
            data_schema=STEP_CONNECTION_TYPE_SCHEMA,
        )

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: Dict[str, str] = {}

        # Always do discovery first to find devices (much simpler UX)
        if not self._discovery_completed:
            return await self.async_step_discovery()

        if user_input is not None:
            try:
                # Handle device selection from discovery
                if "device" in user_input:
                    device_selection = user_input["device"]
                    if device_selection == "manual":
                        # User chose manual entry, rebuild form for manual input
                        return self.async_show_form(
                            step_id="user",
                            data_schema=self._build_user_data_schema(),
                            errors=errors,
                        )
                    else:
                        # Parse selected device and store for connection type selection
                        host, port = device_selection.split(":")
                        self._selected_host = host
                        self._selected_port = int(port)
                        self._selected_username = user_input.get(
                            CONF_USERNAME, DEFAULT_USERNAME
                        )
                        self._selected_password = user_input.get(
                            CONF_PASSWORD, DEFAULT_PASSWORD
                        )

                        # Go to connection type selection
                        return await self.async_step_connection_type()

                # If we get here, user provided manual connection details
                # Store manual input and proceed to connection type selection
                self._manual_input = user_input
                return await self.async_step_connection_type()

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
        """Handle device discovery - now the first step for better UX."""
        _LOGGER.info("Starting RackLink device discovery...")

        try:
            # Discover devices using mDNS
            self._discovered_devices = await discover_racklink_devices(
                self.hass, timeout=8.0
            )
            self._discovery_completed = True

            _LOGGER.info("Discovery found %d devices", len(self._discovered_devices))

            # Always show device selection (discovered + manual option)
            if self._discovered_devices:
                if len(self._discovered_devices) == 1:
                    # Single device found - show it with manual option
                    device = self._discovered_devices[0]
                    return self.async_show_form(
                        step_id="user",
                        data_schema=self._build_single_device_schema(device),
                        description_placeholders={"device_name": device.name},
                    )
                else:
                    # Multiple devices found, let user choose
                    return self.async_show_form(
                        step_id="user",
                        data_schema=self._build_device_selection_schema(),
                        description_placeholders={
                            "discovered_count": str(len(self._discovered_devices))
                        },
                    )
            else:
                # No devices found - offer manual entry
                return self.async_show_form(
                    step_id="user",
                    data_schema=self._build_user_data_schema(),
                    description_placeholders={"discovered_count": "0"},
                )

        except Exception as err:
            _LOGGER.error("Error during discovery: %s", err)
            # On discovery error, fall back to manual entry
            return self.async_show_form(
                step_id="user",
                data_schema=self._build_user_data_schema(),
                description_placeholders={"discovered_count": "0"},
            )

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
        """Build schema for single device with option to use it or enter manually."""
        # Create device options - discovered device + manual entry
        device_key = f"{device.ip_address}:{device.suggested_control_port}"
        device_options = {
            device_key: f"{device.name} ({device.ip_address})",
            "manual": "Enter manually",
        }

        schema_dict = {
            vol.Required("device", default=device_key): vol.In(device_options),
            vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): cv.string,
            vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): cv.string,
        }

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
