"""Connection factory for Middle Atlantic RackLink devices."""

from __future__ import annotations

import logging
from typing import Union

from .const import (
    CONNECTION_TYPE_REDFISH,
    CONNECTION_TYPE_TELNET,
    CONNECTION_TYPE_AUTO,
    CONF_CONNECTION_TYPE,
    CONF_USE_HTTPS,
    DEFAULT_REDFISH_PORT,
    DEFAULT_REDFISH_HTTP_PORT,
)
from .redfish_connection import RedfishConnection, RedfishConfig
from .socket_connection import SocketConnection, SocketConfig

_LOGGER = logging.getLogger(__name__)


class ConnectionFactory:
    """Factory class to create appropriate connection instances."""

    @staticmethod
    def create_connection(
        config_data: dict,
    ) -> Union[RedfishConnection, SocketConnection]:
        """Create a connection instance based on configuration.

        Args:
            config_data: Configuration dictionary containing connection details

        Returns:
            Either a RedfishConnection or SocketConnection instance

        Raises:
            ValueError: If connection type is invalid
        """
        connection_type = config_data.get(CONF_CONNECTION_TYPE, CONNECTION_TYPE_AUTO)
        host = config_data["host"]
        port = config_data["port"]
        username = config_data.get("username")
        password = config_data.get("password")
        timeout = config_data.get("timeout", 20)

        _LOGGER.debug(
            "Creating connection: type=%s, host=%s, port=%d",
            connection_type,
            host,
            port,
        )

        if connection_type == CONNECTION_TYPE_REDFISH:
            return ConnectionFactory._create_redfish_connection(config_data)
        elif connection_type == CONNECTION_TYPE_TELNET:
            return ConnectionFactory._create_socket_connection(config_data)
        elif connection_type == CONNECTION_TYPE_AUTO:
            # Auto-detect: try Redfish first, then fallback to Telnet
            return ConnectionFactory._create_auto_connection(config_data)
        else:
            raise ValueError(f"Unknown connection type: {connection_type}")

    @staticmethod
    def _create_redfish_connection(config_data: dict) -> RedfishConnection:
        """Create a Redfish connection instance."""
        host = config_data["host"]
        port = config_data["port"]
        username = config_data.get("username")
        password = config_data.get("password")
        use_https = config_data.get(CONF_USE_HTTPS, True)
        timeout = config_data.get("timeout", 20)

        # If no specific port is set, use defaults based on HTTPS setting
        if port in [DEFAULT_REDFISH_PORT, DEFAULT_REDFISH_HTTP_PORT] or not port:
            port = DEFAULT_REDFISH_PORT if use_https else DEFAULT_REDFISH_HTTP_PORT

        config = RedfishConfig(
            host=host,
            port=port,
            username=username,
            password=password,
            use_https=use_https,
            timeout=timeout,
        )

        _LOGGER.info(
            "Creating Redfish connection to %s:%d (HTTPS: %s)",
            host,
            port,
            use_https,
        )
        return RedfishConnection(config)

    @staticmethod
    def _create_socket_connection(config_data: dict) -> SocketConnection:
        """Create a socket (Telnet/Binary) connection instance."""
        host = config_data["host"]
        port = config_data["port"]
        username = config_data.get("username")
        password = config_data.get("password")
        timeout = config_data.get("timeout", 20)

        config = SocketConfig(
            host=host,
            port=port,
            username=username,
            password=password,
            timeout=timeout,
        )

        _LOGGER.info("Creating Telnet/Binary connection to %s:%d", host, port)
        return SocketConnection(config)

    @staticmethod
    def _create_auto_connection(
        config_data: dict,
    ) -> Union[RedfishConnection, SocketConnection]:
        """Create connection with auto-detection.

        This method will be used during setup to try Redfish first,
        then fallback to Telnet if Redfish is not available.
        For now, it creates a Redfish connection as a placeholder.
        The actual auto-detection logic will be implemented in the validation phase.
        """
        # For auto mode, we'll prefer Redfish by default
        # The actual detection will happen during connection validation
        _LOGGER.debug("Auto connection mode - will attempt Redfish first")

        # Create a Redfish config first
        redfish_config = dict(config_data)
        redfish_config[CONF_CONNECTION_TYPE] = CONNECTION_TYPE_REDFISH

        # Set default Redfish settings if not specified
        if "port" not in redfish_config or redfish_config["port"] == config_data.get(
            "port"
        ):
            redfish_config["port"] = DEFAULT_REDFISH_PORT
        if CONF_USE_HTTPS not in redfish_config:
            redfish_config[CONF_USE_HTTPS] = True

        return ConnectionFactory._create_redfish_connection(redfish_config)


class AutoConnectionManager:
    """Manager for auto-detection of connection types."""

    @staticmethod
    async def detect_best_connection(
        config_data: dict,
    ) -> Union[RedfishConnection, SocketConnection]:
        """Detect and return the best available connection type.

        Args:
            config_data: Configuration dictionary

        Returns:
            The best available connection instance

        Raises:
            ConnectionError: If no connection type works
        """
        host = config_data["host"]
        username = config_data.get("username")
        password = config_data.get("password")

        _LOGGER.info("Auto-detecting connection type for %s", host)

        # Try Redfish first (both HTTPS and HTTP)
        redfish_configs = [
            {
                **config_data,
                CONF_CONNECTION_TYPE: CONNECTION_TYPE_REDFISH,
                "port": DEFAULT_REDFISH_PORT,
                CONF_USE_HTTPS: True,
            },
            {
                **config_data,
                CONF_CONNECTION_TYPE: CONNECTION_TYPE_REDFISH,
                "port": DEFAULT_REDFISH_HTTP_PORT,
                CONF_USE_HTTPS: False,
            },
        ]

        for redfish_config in redfish_configs:
            try:
                _LOGGER.debug(
                    "Trying Redfish connection: %s:%d (HTTPS: %s)",
                    host,
                    redfish_config["port"],
                    redfish_config[CONF_USE_HTTPS],
                )

                connection = ConnectionFactory._create_redfish_connection(
                    redfish_config
                )
                if await connection.connect():
                    _LOGGER.info(
                        "Successfully connected via Redfish to %s:%d",
                        host,
                        redfish_config["port"],
                    )
                    return connection
                else:
                    await connection.disconnect()
            except Exception as err:
                _LOGGER.debug("Redfish connection failed: %s", err)

        # If Redfish failed, try Telnet/Binary protocols
        _LOGGER.debug("Redfish not available, trying Telnet/Binary protocols")

        # Common Telnet/Binary ports to try
        telnet_ports = [6000, 60000, 23, 4001]

        for port in telnet_ports:
            try:
                telnet_config = {
                    **config_data,
                    CONF_CONNECTION_TYPE: CONNECTION_TYPE_TELNET,
                    "port": port,
                }

                _LOGGER.debug("Trying Telnet/Binary connection: %s:%d", host, port)

                connection = ConnectionFactory._create_socket_connection(telnet_config)
                if await connection.connect():
                    _LOGGER.info(
                        "Successfully connected via Telnet/Binary to %s:%d",
                        host,
                        port,
                    )
                    return connection
                else:
                    await connection.disconnect()
            except Exception as err:
                _LOGGER.debug(
                    "Telnet/Binary connection to port %d failed: %s", port, err
                )

        raise ConnectionError(f"Could not establish any connection type to {host}")
