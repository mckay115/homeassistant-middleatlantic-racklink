"""Test the Middle Atlantic RackLink config flow."""

from custom_components.middle_atlantic_racklink.config_flow import (
    CannotConnect,
    InvalidAuth,
    MiddleAtlanticRacklinkConfigFlow,
)
from custom_components.middle_atlantic_racklink.const import DOMAIN
from homeassistant import config_entries, data_entry_flow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from unittest.mock import MagicMock, patch

import pytest

PDU_INFO = {
    "pdu_name": "Test PDU",
    "pdu_model": "Test Model",
    "pdu_firmware": "1.0",
    "pdu_serial": "123456",
    "mac_address": "00:11:22:33:44:55",
}

PDU_INFO_NO_MAC = {
    "pdu_name": "Test PDU",
    "pdu_model": "Test Model",
    "pdu_firmware": "1.0",
    "pdu_serial": "123456",
    "mac_address": "Unknown MAC",
}


@pytest.fixture
def mock_setup_entry():
    """Mock setting up a config entry."""
    with patch(
        "custom_components.middle_atlantic_racklink.async_setup_entry",
        return_value=True,
    ):
        yield


@pytest.fixture
def mock_hass():
    """Mock Home Assistant instance."""
    hass = MagicMock()
    hass.config_entries = MagicMock()
    hass.config_entries.flow = MagicMock()
    hass.config_entries.flow.async_progress_by_handler = MagicMock(return_value=[])
    hass.config_entries.async_entries = MagicMock(return_value=[])
    hass.data = {DOMAIN: {}}
    return hass


@pytest.fixture
def flow(mock_hass):
    """Initialize a config flow."""
    flow = MiddleAtlanticRacklinkConfigFlow()
    flow.hass = mock_hass
    flow.context = {}
    flow._unique_id = None  # Ensure unique_id is reset
    return flow


@pytest.mark.asyncio
async def test_user_success(flow):
    """Test successful user flow."""
    with patch(
        "custom_components.middle_atlantic_racklink.config_flow.validate_connection",
        return_value=PDU_INFO_NO_MAC,
    ):
        result = await flow.async_step_user(
            {
                CONF_HOST: "test_host",
                CONF_PORT: 23,
                CONF_USERNAME: "test_user",
                CONF_PASSWORD: "test_pass",
            }
        )

        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert (
            result["title"]
            == f"{PDU_INFO_NO_MAC['pdu_name']} ({PDU_INFO_NO_MAC['pdu_model']})"
        )
        assert result["data"] == {
            CONF_HOST: "test_host",
            CONF_PORT: 23,
            CONF_USERNAME: "test_user",
            CONF_PASSWORD: "test_pass",
        }


@pytest.mark.asyncio
async def test_user_connection_error(flow):
    """Test connection error in user flow."""
    with patch(
        "custom_components.middle_atlantic_racklink.config_flow.validate_connection",
        side_effect=CannotConnect("Connection failed"),
    ):
        result = await flow.async_step_user(
            {
                CONF_HOST: "test_host",
                CONF_PORT: 23,
                CONF_USERNAME: "test_user",
                CONF_PASSWORD: "test_pass",
            }
        )

        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_user_invalid_input(flow):
    """Test invalid input in user flow."""
    result = await flow.async_step_user(
        {
            CONF_HOST: "",  # Empty host should be invalid
            CONF_PORT: 23,
            CONF_USERNAME: "test_user",
            CONF_PASSWORD: "test_pass",
        }
    )

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_import_success(flow):
    """Test successful import flow."""
    with patch(
        "custom_components.middle_atlantic_racklink.config_flow.validate_connection",
        return_value=PDU_INFO_NO_MAC,  # Use PDU info without MAC to avoid unique ID check
    ):
        result = await flow.async_step_import(
            {
                CONF_HOST: "test_host",
                CONF_PORT: 23,
                CONF_USERNAME: "test_user",
                CONF_PASSWORD: "test_pass",
            }
        )

        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert (
            result["title"]
            == f"{PDU_INFO_NO_MAC['pdu_name']} ({PDU_INFO_NO_MAC['pdu_model']})"
        )
        assert result["data"] == {
            CONF_HOST: "test_host",
            CONF_PORT: 23,
            CONF_USERNAME: "test_user",
            CONF_PASSWORD: "test_pass",
        }


@pytest.mark.asyncio
async def test_import_connection_error(flow):
    """Test connection error in import flow."""
    with patch(
        "custom_components.middle_atlantic_racklink.config_flow.validate_connection",
        side_effect=CannotConnect("Connection failed"),
    ):
        result = await flow.async_step_import(
            {
                CONF_HOST: "test_host",
                CONF_PORT: 23,
                CONF_USERNAME: "test_user",
                CONF_PASSWORD: "test_pass",
            }
        )

        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_duplicate_error(mock_hass):
    """Test duplicate entry error."""
    # Create a mock entry with the same MAC address
    mock_entry = MagicMock()
    mock_entry.unique_id = PDU_INFO["mac_address"]
    mock_hass.config_entries.async_entries = MagicMock(return_value=[mock_entry])

    # Create a new flow with the mock entry
    flow = MiddleAtlanticRacklinkConfigFlow()
    flow.hass = mock_hass
    flow.context = {}
    flow._unique_id = None  # Ensure unique_id is reset

    # Try to add an entry with the same MAC address
    with patch(
        "custom_components.middle_atlantic_racklink.config_flow.validate_connection",
        return_value=PDU_INFO,
    ):
        result = await flow.async_step_user(
            {
                CONF_HOST: "test_host",
                CONF_PORT: 23,
                CONF_USERNAME: "test_user",
                CONF_PASSWORD: "test_pass",
            }
        )

        assert result["type"] == data_entry_flow.FlowResultType.ABORT
        assert result["reason"] == "already_configured"
