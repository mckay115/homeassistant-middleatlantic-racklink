"""Test the Middle Atlantic RackLink config flow."""

from __future__ import annotations

from .conftest import MOCK_CONFIG, MOCK_PDU_INFO
from custom_components.middle_atlantic_racklink.config_flow import (
    CannotConnect,
    InvalidAuth,
)
from custom_components.middle_atlantic_racklink.const import (
    CONF_CONNECTION_TYPE,
    CONF_SCAN_INTERVAL,
    CONF_USE_HTTPS,
    CONNECTION_TYPE_REDFISH,
    CONNECTION_TYPE_TELNET,
    DEFAULT_PORT,
    DEFAULT_REDFISH_PORT,
    DOMAIN,
)
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from ipaddress import ip_address
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unittest.mock import patch

import pytest

USER_INPUT = {
    CONF_HOST: "192.168.1.100",
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "secret",
}

ZEROCONF_DISCOVERY = ZeroconfServiceInfo(
    ip_address=ip_address("192.168.1.100"),
    ip_addresses=[ip_address("192.168.1.100")],
    hostname="racklink-pdu.local.",
    name="racklink-pdu._http._tcp.local.",
    port=443,
    properties={},
    type="_http._tcp.local.",
)


@pytest.fixture(autouse=True)
def mock_discovery():
    """Skip mDNS discovery during the user step."""
    with patch(
        "custom_components.middle_atlantic_racklink.config_flow."
        "discover_racklink_devices",
        return_value=[],
    ):
        yield


@pytest.fixture
def mock_setup_entry():
    """Mock setting up a config entry."""
    with patch(
        "custom_components.middle_atlantic_racklink.async_setup_entry",
        return_value=True,
    ) as mock:
        yield mock


@pytest.fixture
def mock_validate():
    """Mock a successful connection validation."""
    with patch(
        "custom_components.middle_atlantic_racklink.config_flow." "validate_connection",
        return_value=dict(MOCK_PDU_INFO),
    ) as mock:
        yield mock


async def test_user_flow_success(
    hass: HomeAssistant, mock_setup_entry, mock_validate
) -> None:
    """Test the full manual user flow creates an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "connection_type"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONNECTION_TYPE: CONNECTION_TYPE_REDFISH}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test PDU (RLNK-P920R)"
    assert result["data"][CONF_HOST] == "192.168.1.100"
    assert result["data"][CONF_PORT] == DEFAULT_REDFISH_PORT
    assert result["data"][CONF_USE_HTTPS] is True
    assert result["data"][CONF_CONNECTION_TYPE] == CONNECTION_TYPE_REDFISH
    assert result["result"].unique_id == MOCK_PDU_INFO["mac_address"]
    assert len(mock_setup_entry.mock_calls) == 1


async def test_user_flow_telnet_port(
    hass: HomeAssistant, mock_setup_entry, mock_validate
) -> None:
    """Test selecting telnet uses the telnet default port."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONNECTION_TYPE: CONNECTION_TYPE_TELNET}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PORT] == DEFAULT_PORT


async def test_user_flow_cannot_connect_then_recover(
    hass: HomeAssistant, mock_setup_entry
) -> None:
    """Test a connection error shows the form again and can be retried."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    with patch(
        "custom_components.middle_atlantic_racklink.config_flow." "validate_connection",
        side_effect=CannotConnect("boom"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CONNECTION_TYPE: CONNECTION_TYPE_TELNET}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "connection_type"
    assert result["errors"] == {"base": "cannot_connect"}

    with patch(
        "custom_components.middle_atlantic_racklink.config_flow." "validate_connection",
        return_value=dict(MOCK_PDU_INFO),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CONNECTION_TYPE: CONNECTION_TYPE_TELNET}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_invalid_auth(hass: HomeAssistant) -> None:
    """Test invalid credentials show an invalid_auth error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    with patch(
        "custom_components.middle_atlantic_racklink.config_flow." "validate_connection",
        side_effect=InvalidAuth("denied"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CONNECTION_TYPE: CONNECTION_TYPE_TELNET}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_unknown_error(hass: HomeAssistant) -> None:
    """Test unexpected exceptions map to the unknown error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    with patch(
        "custom_components.middle_atlantic_racklink.config_flow." "validate_connection",
        side_effect=RuntimeError("boom"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CONNECTION_TYPE: CONNECTION_TYPE_TELNET}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}


async def test_user_flow_duplicate_aborts(hass: HomeAssistant, mock_validate) -> None:
    """Test configuring an already-configured device aborts."""
    MockConfigEntry(
        domain=DOMAIN,
        data=dict(MOCK_CONFIG),
        unique_id=MOCK_PDU_INFO["mac_address"],
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONNECTION_TYPE: CONNECTION_TYPE_REDFISH}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_zeroconf_flow_success(
    hass: HomeAssistant, mock_setup_entry, mock_validate
) -> None:
    """Test the zeroconf discovery flow creates an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=ZEROCONF_DISCOVERY,
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "zeroconf_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "connection_type"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONNECTION_TYPE: CONNECTION_TYPE_REDFISH}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == MOCK_PDU_INFO["mac_address"]


async def test_zeroconf_ignores_other_devices(hass: HomeAssistant) -> None:
    """Test zeroconf discovery of a non-RackLink device aborts."""
    discovery = ZeroconfServiceInfo(
        ip_address=ip_address("192.168.1.50"),
        ip_addresses=[ip_address("192.168.1.50")],
        hostname="printer.local.",
        name="printer._http._tcp.local.",
        port=80,
        properties={},
        type="_http._tcp.local.",
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=discovery,
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_racklink_device"


async def test_zeroconf_already_configured_updates_host(
    hass: HomeAssistant,
) -> None:
    """Test zeroconf discovery of a configured device aborts and updates host."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**MOCK_CONFIG, CONF_HOST: "192.168.1.99"},
        unique_id="racklink-pdu.local",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=ZEROCONF_DISCOVERY,
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.168.1.100"


async def test_reauth_flow_success(
    hass: HomeAssistant, mock_setup_entry, mock_validate
) -> None:
    """Test reauthentication updates the stored credentials."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=dict(MOCK_CONFIG),
        unique_id=MOCK_PDU_INFO["mac_address"],
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "admin", CONF_PASSWORD: "new-password"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new-password"


async def test_reauth_flow_invalid_auth(hass: HomeAssistant) -> None:
    """Test reauthentication with bad credentials shows an error."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=dict(MOCK_CONFIG),
        unique_id=MOCK_PDU_INFO["mac_address"],
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)

    with patch(
        "custom_components.middle_atlantic_racklink.config_flow." "validate_connection",
        side_effect=InvalidAuth("denied"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "admin", CONF_PASSWORD: "still-wrong"},
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_reconfigure_flow_success(
    hass: HomeAssistant, mock_setup_entry, mock_validate
) -> None:
    """Test reconfiguring host and connection type."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=dict(MOCK_CONFIG),
        unique_id=MOCK_PDU_INFO["mac_address"],
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "192.168.1.200",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_TELNET,
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "192.168.1.200"
    assert entry.data[CONF_CONNECTION_TYPE] == CONNECTION_TYPE_TELNET
    assert entry.data[CONF_PORT] == DEFAULT_PORT


async def test_reconfigure_flow_cannot_connect(hass: HomeAssistant) -> None:
    """Test reconfigure shows an error when the new host is unreachable."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=dict(MOCK_CONFIG),
        unique_id=MOCK_PDU_INFO["mac_address"],
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)

    with patch(
        "custom_components.middle_atlantic_racklink.config_flow." "validate_connection",
        side_effect=CannotConnect("boom"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "192.168.1.201",
                CONF_USERNAME: "admin",
                CONF_PASSWORD: "secret",
                CONF_CONNECTION_TYPE: CONNECTION_TYPE_TELNET,
            },
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_options_flow(hass: HomeAssistant, mock_config_entry) -> None:
    """Test the options flow stores the scan interval."""
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 42}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options[CONF_SCAN_INTERVAL] == 42
