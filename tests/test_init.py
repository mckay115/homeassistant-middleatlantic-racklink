"""Test Middle Atlantic RackLink integration setup and unload."""

from __future__ import annotations

from .conftest import MOCK_PDU_INFO
from custom_components.middle_atlantic_racklink.const import (
    ATTR_MANUFACTURER,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)
from custom_components.middle_atlantic_racklink.coordinator import (
    RacklinkCoordinator,
)
from custom_components.middle_atlantic_racklink.exceptions import (
    RacklinkAuthenticationError,
)
from datetime import timedelta
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from unittest.mock import MagicMock


async def test_setup_and_unload_entry(
    hass: HomeAssistant, mock_config_entry, mock_controller: MagicMock
) -> None:
    """Test a normal setup and unload cycle."""
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    coordinator = mock_config_entry.runtime_data
    assert isinstance(coordinator, RacklinkCoordinator)
    assert coordinator.system_data["voltage"] == 120.0
    assert coordinator.outlet_data[1]["state"] is True
    assert coordinator.status_data["surge_protection_ok"] is True

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    mock_controller.disconnect.assert_awaited()


async def test_setup_retry_on_connection_failure(
    hass: HomeAssistant, mock_config_entry, mock_controller: MagicMock
) -> None:
    """Test setup retries when the PDU cannot be reached."""
    mock_controller.connected = False
    mock_controller.connect.return_value = False

    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_auth_failure_starts_reauth(
    hass: HomeAssistant, mock_config_entry, mock_controller: MagicMock
) -> None:
    """Test setup with rejected credentials triggers reauth."""
    mock_controller.connected = False
    mock_controller.connect.side_effect = RacklinkAuthenticationError("denied")

    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert any(
        flow["context"].get("source") == "reauth"
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    )


async def test_update_failure_marks_entities_unavailable(
    hass: HomeAssistant, mock_config_entry, mock_controller: MagicMock
) -> None:
    """Test a failed poll disconnects for a clean reconnect."""
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = mock_config_entry.runtime_data
    mock_controller.update.return_value = False

    await coordinator.async_refresh()
    assert coordinator.last_update_success is False
    mock_controller.disconnect.assert_awaited()


async def test_options_update_changes_scan_interval(
    hass: HomeAssistant, mock_config_entry, mock_controller: MagicMock
) -> None:
    """Test changing options adjusts the coordinator interval in place."""
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = mock_config_entry.runtime_data
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_SCAN_INTERVAL: 42}
    )
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert coordinator.update_interval == timedelta(seconds=42)


async def test_device_registry_entry(
    hass: HomeAssistant, mock_config_entry, mock_controller: MagicMock
) -> None:
    """Test the PDU device is registered with correct metadata."""
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(
        identifiers={(DOMAIN, MOCK_PDU_INFO["pdu_serial"])}
    )
    assert device is not None
    assert device.name == MOCK_PDU_INFO["pdu_name"]
    assert device.manufacturer == ATTR_MANUFACTURER
    assert device.model == MOCK_PDU_INFO["pdu_model"]
    assert device.sw_version == MOCK_PDU_INFO["pdu_firmware"]
    assert (
        dr.CONNECTION_NETWORK_MAC,
        MOCK_PDU_INFO["mac_address"],
    ) in device.connections
