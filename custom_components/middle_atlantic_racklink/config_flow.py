"""Config flow for Middle Atlantic RackLink."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import asyncio
import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .const import (
    CONF_CONNECTION_TYPE,
    CONF_ENABLE_VENDOR_FEATURES,
    CONF_SCAN_INTERVAL,
    CONF_USE_HTTPS,
    CONNECTION_TYPE_AUTO,
    CONNECTION_TYPE_REDFISH,
    CONNECTION_TYPE_TELNET,
    DEFAULT_PORT,
    DEFAULT_REDFISH_HTTP_PORT,
    DEFAULT_REDFISH_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USERNAME,
    DOMAIN,
)
from .controller.racklink_controller import RacklinkController
from .discovery import DiscoveredDevice, discover_racklink_devices
from .exceptions import RacklinkAuthenticationError

_LOGGER = logging.getLogger(__name__)

CONNECTION_TIMEOUT = 10

STEP_CONNECTION_TYPE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CONNECTION_TYPE, default=CONNECTION_TYPE_REDFISH): vol.In(
            [
                CONNECTION_TYPE_REDFISH,
                CONNECTION_TYPE_TELNET,
                CONNECTION_TYPE_AUTO,
            ]
        ),
    }
)


async def validate_connection(
    _hass: HomeAssistant, data: Dict[str, Any]
) -> Dict[str, Any]:
    """Validate the connection to the RackLink PDU.

    Returns a dict with device metadata on success.

    Raises:
        CannotConnect: If the device cannot be reached.
        InvalidAuth: If the device rejects the credentials.
    """
    host = data.get(CONF_HOST)
    port = data.get(CONF_PORT, DEFAULT_PORT)
    username = data.get(CONF_USERNAME)
    password = data.get(CONF_PASSWORD)
    connection_type = data.get(CONF_CONNECTION_TYPE, CONNECTION_TYPE_AUTO)
    use_https = data.get(CONF_USE_HTTPS, True)

    if host is None or (isinstance(host, str) and not host.strip()):
        raise CannotConnect("Host is required")

    _LOGGER.debug(
        "Validating connection: host=%s, port=%s, connection_type=%s, use_https=%s",
        host,
        port,
        connection_type,
        use_https,
    )

    controller = RacklinkController(
        host=host,
        port=port,
        username=username,
        password=password,
        timeout=CONNECTION_TIMEOUT,
        connection_type=connection_type,
        use_https=use_https,
        enable_vendor_features=data.get(CONF_ENABLE_VENDOR_FEATURES, True),
    )

    try:
        if not await controller.connect():
            raise CannotConnect("Connection failed")

        # Retrieve device information
        await controller.update()

        if (
            not controller.connection.connected
            or not controller.connection.authenticated
        ):
            raise InvalidAuth("Authentication failed")

        info = {
            "pdu_name": controller.pdu_name or "RackLink PDU",
            "pdu_model": controller.pdu_model or "Unknown Model",
            "pdu_firmware": controller.pdu_firmware or "Unknown Firmware",
            "pdu_serial": controller.pdu_serial or "Unknown Serial",
            "mac_address": controller.mac_address or None,
        }

        _LOGGER.info(
            "Successfully connected to %s (%s)", info["pdu_name"], info["pdu_model"]
        )
        return info

    except RacklinkAuthenticationError as exc:
        raise InvalidAuth(str(exc)) from exc
    except (CannotConnect, InvalidAuth):
        raise
    except asyncio.TimeoutError as exc:
        raise CannotConnect("Connection timeout") from exc
    except Exception as exc:
        raise CannotConnect(f"Error connecting to device: {exc}") from exc
    finally:
        await controller.disconnect()


class MiddleAtlanticRacklinkConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Middle Atlantic RackLink."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_devices: List[DiscoveredDevice] = []
        self._discovery_completed = False

        # Connection details gathered across steps
        self._pending_input: Optional[Dict[str, Any]] = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "OptionsFlowHandler":
        """Get the options flow for this handler."""
        return OptionsFlowHandler()

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> FlowResult:
        """Handle zeroconf discovery."""
        hostname = (discovery_info.hostname or "").rstrip(".")
        _LOGGER.debug("Zeroconf discovery: %s (%s)", hostname, discovery_info.host)

        if not any(
            identifier in hostname.lower()
            for identifier in ("racklink", "pdu", "power")
        ):
            return self.async_abort(reason="not_racklink_device")

        # Set a provisional unique ID from the hostname; it is replaced with
        # the MAC address once the device has been validated.
        await self.async_set_unique_id(hostname)
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: discovery_info.host}
        )

        self._pending_input = {CONF_HOST: discovery_info.host}
        self.context["title_placeholders"] = {"name": hostname}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Confirm zeroconf discovery and collect credentials."""
        if user_input is not None:
            self._pending_input = {
                CONF_HOST: user_input[CONF_HOST],
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            return await self.async_step_connection_type()

        host = self._pending_input.get(CONF_HOST) if self._pending_input else None

        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=host): cv.string,
                vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
            }
        )

        return self.async_show_form(
            step_id="zeroconf_confirm",
            data_schema=data_schema,
            description_placeholders={"hostname": host or ""},
        )

    async def async_step_connection_type(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle connection type selection and validate the connection."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            if not self._pending_input:
                _LOGGER.warning("No host selected or entered; returning to user step")
                return await self.async_step_user()

            connection_type = user_input[CONF_CONNECTION_TYPE]
            final_input = dict(self._pending_input)
            final_input[CONF_CONNECTION_TYPE] = connection_type

            # Set port and protocol based on connection type
            if connection_type == CONNECTION_TYPE_REDFISH:
                final_input[CONF_PORT] = DEFAULT_REDFISH_PORT
                final_input[CONF_USE_HTTPS] = True
                final_input.setdefault(CONF_ENABLE_VENDOR_FEATURES, True)
            elif connection_type == CONNECTION_TYPE_TELNET:
                final_input[CONF_PORT] = DEFAULT_PORT
            else:
                # Auto detection ignores the configured port
                final_input.setdefault(CONF_PORT, DEFAULT_PORT)

            try:
                info = await self._validate_with_fallback(final_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected error during validation")
                errors["base"] = "unknown"
            else:
                return await self._async_create_or_update_entry(info, final_input)

        return self.async_show_form(
            step_id="connection_type",
            data_schema=STEP_CONNECTION_TYPE_SCHEMA,
            errors=errors,
        )

    async def _validate_with_fallback(
        self, final_input: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Validate the connection, falling back to HTTP for Redfish."""
        try:
            return await validate_connection(self.hass, final_input)
        except CannotConnect:
            if final_input.get(
                CONF_CONNECTION_TYPE
            ) == CONNECTION_TYPE_REDFISH and final_input.get(CONF_USE_HTTPS):
                _LOGGER.debug("HTTPS failed, trying HTTP for Redfish connection")
                final_input[CONF_PORT] = DEFAULT_REDFISH_HTTP_PORT
                final_input[CONF_USE_HTTPS] = False
                return await validate_connection(self.hass, final_input)
            raise

    async def _async_create_or_update_entry(
        self, info: Dict[str, Any], final_input: Dict[str, Any]
    ) -> FlowResult:
        """Create the config entry after successful validation."""
        mac_address = info.get("mac_address")
        if mac_address:
            await self.async_set_unique_id(mac_address, raise_on_progress=False)
            self._abort_if_unique_id_configured(updates=final_input)

        title = info["pdu_name"]
        if info["pdu_model"] != "Unknown Model":
            title = f"{title} ({info['pdu_model']})"

        return self.async_create_entry(title=title, data=final_input)

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: Dict[str, str] = {}

        # Discover devices first for a better UX
        if not self._discovery_completed:
            await self._async_run_discovery()

        if user_input is not None:
            device_selection = user_input.get("device")
            if device_selection == "manual":
                return self.async_show_form(
                    step_id="user",
                    data_schema=self._build_user_data_schema(),
                    errors=errors,
                )

            self._pending_input = {
                CONF_HOST: device_selection or user_input.get(CONF_HOST),
                CONF_USERNAME: user_input.get(CONF_USERNAME, DEFAULT_USERNAME),
                CONF_PASSWORD: user_input.get(CONF_PASSWORD, ""),
            }
            return await self.async_step_connection_type()

        if self._discovered_devices:
            return self.async_show_form(
                step_id="user",
                data_schema=self._build_device_selection_schema(),
                errors=errors,
                description_placeholders={
                    "discovered_count": str(len(self._discovered_devices))
                },
            )
        return self.async_show_form(
            step_id="user",
            data_schema=self._build_user_data_schema(),
            errors=errors,
            description_placeholders={"discovered_count": "0"},
        )

    async def _async_run_discovery(self) -> None:
        """Run mDNS discovery for RackLink devices."""
        _LOGGER.debug("Starting RackLink device discovery")
        try:
            self._discovered_devices = await discover_racklink_devices(
                self.hass, timeout=8.0
            )
            _LOGGER.debug("Discovery found %d devices", len(self._discovered_devices))
        except Exception as err:
            _LOGGER.debug("Error during discovery: %s", err)
            self._discovered_devices = []
        finally:
            self._discovery_completed = True

    def _build_user_data_schema(self) -> vol.Schema:
        """Build schema for manual entry."""
        return vol.Schema(
            {
                vol.Required(CONF_HOST): cv.string,
                vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
            }
        )

    def _build_device_selection_schema(self) -> vol.Schema:
        """Build schema with discovered devices."""
        device_options = {
            device.ip_address: f"{device.name} ({device.ip_address})"
            for device in self._discovered_devices
        }
        device_options["manual"] = "Enter manually"

        return vol.Schema(
            {
                vol.Required("device"): vol.In(device_options),
                vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
            }
        )

    async def async_step_reauth(
        self, entry_data: Dict[str, Any]
    ) -> FlowResult:
        """Handle reauthentication when the device rejects the credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Collect new credentials for reauthentication."""
        errors: Dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            data = {**reauth_entry.data, **user_input}
            try:
                await validate_connection(self.hass, data)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected error during reauth")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry, data_updates=user_input
                )

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_USERNAME,
                    default=reauth_entry.data.get(CONF_USERNAME, DEFAULT_USERNAME),
                ): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
            }
        )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "host": reauth_entry.data.get(CONF_HOST, "")
            },
        )

    async def async_step_reconfigure(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle reconfiguration of an existing entry."""
        errors: Dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()

        if user_input is not None:
            data = {**reconfigure_entry.data, **user_input}
            connection_type = data.get(CONF_CONNECTION_TYPE, CONNECTION_TYPE_AUTO)

            # Re-derive port/protocol from the selected connection type
            if connection_type == CONNECTION_TYPE_REDFISH:
                data[CONF_PORT] = DEFAULT_REDFISH_PORT
                data[CONF_USE_HTTPS] = True
            elif connection_type == CONNECTION_TYPE_TELNET:
                data[CONF_PORT] = DEFAULT_PORT

            try:
                info = await self._validate_with_fallback(data)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected error during reconfigure")
                errors["base"] = "unknown"
            else:
                mac_address = info.get("mac_address")
                if mac_address:
                    await self.async_set_unique_id(mac_address)
                    self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    reconfigure_entry, data_updates=data
                )

        current = reconfigure_entry.data
        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=current.get(CONF_HOST)): cv.string,
                vol.Required(
                    CONF_USERNAME,
                    default=current.get(CONF_USERNAME, DEFAULT_USERNAME),
                ): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
                vol.Required(
                    CONF_CONNECTION_TYPE,
                    default=current.get(CONF_CONNECTION_TYPE, CONNECTION_TYPE_AUTO),
                ): vol.In(
                    [
                        CONNECTION_TYPE_REDFISH,
                        CONNECTION_TYPE_TELNET,
                        CONNECTION_TYPE_AUTO,
                    ]
                ),
            }
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=data_schema,
            errors=errors,
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Middle Atlantic RackLink."""

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


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
