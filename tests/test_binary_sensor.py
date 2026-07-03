"""Test the Middle Atlantic RackLink binary sensor platform."""

from __future__ import annotations

from .conftest import MOCK_PDU_INFO
from custom_components.middle_atlantic_racklink.const import DOMAIN
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from unittest.mock import MagicMock

SERIAL = MOCK_PDU_INFO["pdu_serial"]


def _get_state(hass: HomeAssistant, unique_id: str):
    """Return the state object for a binary sensor by its unique ID."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("binary_sensor", DOMAIN, unique_id)
    assert entity_id is not None, f"No binary sensor registered for {unique_id}"
    return hass.states.get(entity_id)


async def test_status_binary_sensors(
    hass: HomeAssistant, mock_config_entry, mock_controller: MagicMock
) -> None:
    """Test PDU status binary sensors reflect coordinator data."""
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert _get_state(hass, f"{SERIAL}_surge_protection").state == STATE_ON
    assert _get_state(hass, f"{SERIAL}_load_shedding").state == STATE_OFF
    assert _get_state(hass, f"{SERIAL}_sequence").state == STATE_OFF


async def test_unknown_status_is_unknown(
    hass: HomeAssistant, mock_config_entry, mock_controller: MagicMock
) -> None:
    """Test missing device data yields unknown, not a fabricated default."""
    mock_controller.surge_protection_ok = None
    mock_controller.load_shedding_active = None

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert _get_state(hass, f"{SERIAL}_surge_protection").state == STATE_UNKNOWN
    assert _get_state(hass, f"{SERIAL}_load_shedding").state == STATE_UNKNOWN


async def test_outlet_non_critical_sensors(
    hass: HomeAssistant, mock_config_entry, mock_controller: MagicMock
) -> None:
    """Test per-outlet non-critical binary sensors."""
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert _get_state(hass, f"{SERIAL}_outlet_1_non_critical").state == STATE_OFF
    assert _get_state(hass, f"{SERIAL}_outlet_2_non_critical").state == STATE_ON


async def test_binary_sensors_follow_updates(
    hass: HomeAssistant, mock_config_entry, mock_controller: MagicMock
) -> None:
    """Test binary sensors update when coordinator data changes."""
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    mock_controller.load_shedding_active = True
    coordinator = mock_config_entry.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert _get_state(hass, f"{SERIAL}_load_shedding").state == STATE_ON
