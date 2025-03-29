"""Test the config flow."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.components.middle_atlantic_racklink.const import DOMAIN

from custom_components.middle_atlantic_racklink.config_flow import ConfigFlow


@pytest.fixture
def flow():
    """Create a config flow."""
    return ConfigFlow()


@pytest.mark.asyncio
async def test_user_success(flow):
    """Test successful user flow."""
    with patch(
        "custom_components.middle_atlantic_racklink.config_flow.RacklinkController"
    ) as mock_controller:
        mock_controller.return_value.connect = AsyncMock()
        mock_controller.return_value.disconnect = AsyncMock()

        result = await flow.async_step_user(
            {
                "host": "test_host",
                "port": 23,
                "username": "test_user",
                "password": "test_pass",
            }
        )

        assert result["type"] == config_entries.ConfigEntryType.CREATE_ENTRY
        assert result["data"] == {
            "host": "test_host",
            "port": 23,
            "username": "test_user",
            "password": "test_pass",
        }


@pytest.mark.asyncio
async def test_user_connection_error(flow):
    """Test connection error in user flow."""
    with patch(
        "custom_components.middle_atlantic_racklink.config_flow.RacklinkController"
    ) as mock_controller:
        mock_controller.return_value.connect = AsyncMock(
            side_effect=ValueError("Connection failed")
        )

        result = await flow.async_step_user(
            {
                "host": "test_host",
                "port": 23,
                "username": "test_user",
                "password": "test_pass",
            }
        )

        assert result["type"] == config_entries.ConfigEntryType.FORM
        assert result["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_user_invalid_input(flow):
    """Test invalid input in user flow."""
    result = await flow.async_step_user(
        {
            "host": "",
            "port": 23,
            "username": "test_user",
            "password": "test_pass",
        }
    )

    assert result["type"] == config_entries.ConfigEntryType.FORM
    assert result["errors"]["host"] == "required"


@pytest.mark.asyncio
async def test_import_success(flow):
    """Test successful import flow."""
    with patch(
        "custom_components.middle_atlantic_racklink.config_flow.RacklinkController"
    ) as mock_controller:
        mock_controller.return_value.connect = AsyncMock()
        mock_controller.return_value.disconnect = AsyncMock()

        result = await flow.async_step_import(
            {
                "host": "test_host",
                "port": 23,
                "username": "test_user",
                "password": "test_pass",
            }
        )

        assert result["type"] == config_entries.ConfigEntryType.CREATE_ENTRY
        assert result["data"] == {
            "host": "test_host",
            "port": 23,
            "username": "test_user",
            "password": "test_pass",
        }


@pytest.mark.asyncio
async def test_import_connection_error(flow):
    """Test connection error in import flow."""
    with patch(
        "custom_components.middle_atlantic_racklink.config_flow.RacklinkController"
    ) as mock_controller:
        mock_controller.return_value.connect = AsyncMock(
            side_effect=ValueError("Connection failed")
        )

        result = await flow.async_step_import(
            {
                "host": "test_host",
                "port": 23,
                "username": "test_user",
                "password": "test_pass",
            }
        )

        assert result["type"] == config_entries.ConfigEntryType.ABORT
        assert result["reason"] == "cannot_connect"


@pytest.mark.asyncio
async def test_duplicate_error(flow):
    """Test duplicate entry error."""
    flow.hass = AsyncMock()
    flow.hass.config_entries.async_entries.return_value = [
        config_entries.ConfigEntry(
            entry_id="test_id",
            domain=DOMAIN,
            data={
                "host": "test_host",
                "port": 23,
                "username": "test_user",
                "password": "test_pass",
            },
        )
    ]

    result = await flow.async_step_user(
        {
            "host": "test_host",
            "port": 23,
            "username": "test_user",
            "password": "test_pass",
        }
    )

    assert result["type"] == config_entries.ConfigEntryType.ABORT
    assert result["reason"] == "already_configured"
