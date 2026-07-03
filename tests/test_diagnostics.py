"""Test the Middle Atlantic RackLink diagnostics."""

from __future__ import annotations

from .conftest import MOCK_PDU_INFO
from custom_components.middle_atlantic_racklink.diagnostics import (
    async_get_config_entry_diagnostics,
)
from homeassistant.components.diagnostics import REDACTED
from homeassistant.core import HomeAssistant
from unittest.mock import MagicMock


async def test_diagnostics(
    hass: HomeAssistant, mock_config_entry, mock_controller: MagicMock
) -> None:
    """Test diagnostics output redacts secrets and includes device data."""
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert diagnostics["config_entry"]["host"] == REDACTED
    assert diagnostics["config_entry"]["password"] == REDACTED
    assert diagnostics["config_entry"]["username"] == REDACTED

    device = diagnostics["device"]
    assert device["model"] == MOCK_PDU_INFO["pdu_model"]
    assert device["firmware"] == MOCK_PDU_INFO["pdu_firmware"]
    assert device["outlet_count"] == 2
    assert device["connected"] is True

    coordinator = diagnostics["coordinator"]
    assert coordinator["last_update_success"] is True
    assert coordinator["data"]["system"]["voltage"] == 120.0
