"""Test the Middle Atlantic RackLink switch platform."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_NAME,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.middle_atlantic_racklink.const import (
    DOMAIN,
    SERVICE_CYCLE_ALL_OUTLETS,
    SERVICE_CYCLE_OUTLET,
    SERVICE_SET_OUTLET_NAME,
    SERVICE_SET_PDU_NAME,
)

from .conftest import MOCK_PDU_INFO

SERIAL = MOCK_PDU_INFO["pdu_serial"]


def _entity_id(hass: HomeAssistant, outlet: int) -> str:
    """Return the entity ID of an outlet switch."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "switch", DOMAIN, f"{SERIAL}_outlet_{outlet}"
    )
    assert entity_id is not None
    return entity_id


@pytest.fixture
async def setup_entry(hass: HomeAssistant, mock_config_entry, mock_controller):
    """Set up the integration for switch tests."""
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry


async def test_outlet_switch_states(
    hass: HomeAssistant, setup_entry, mock_controller: MagicMock
) -> None:
    """Test switches are created from real outlet data with correct state."""
    state_1 = hass.states.get(_entity_id(hass, 1))
    assert state_1.state == STATE_ON
    assert state_1.attributes["outlet_number"] == 1
    assert state_1.attributes["outlet_name"] == "Firewall"

    assert hass.states.get(_entity_id(hass, 2)).state == STATE_OFF

    # No hardcoded 8-outlet fallback: only real outlets exist
    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("switch", DOMAIN, f"{SERIAL}_outlet_3")
        is None
    )


async def test_turn_on_off(
    hass: HomeAssistant, setup_entry, mock_controller: MagicMock
) -> None:
    """Test turning an outlet off and on with optimistic state updates."""
    entity_id = _entity_id(hass, 1)

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    mock_controller.turn_outlet_off.assert_awaited_once_with(1)
    assert hass.states.get(entity_id).state == STATE_OFF

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    mock_controller.turn_outlet_on.assert_awaited_once_with(1)
    assert hass.states.get(entity_id).state == STATE_ON


async def test_command_failure_raises(
    hass: HomeAssistant, setup_entry, mock_controller: MagicMock
) -> None:
    """Test a rejected command raises HomeAssistantError."""
    mock_controller.turn_outlet_off.return_value = False

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: _entity_id(hass, 1)},
            blocking=True,
        )
    # State was not optimistically flipped on failure
    assert hass.states.get(_entity_id(hass, 1)).state == STATE_ON


async def test_cycle_outlet_service(
    hass: HomeAssistant, setup_entry, mock_controller: MagicMock
) -> None:
    """Test the cycle_outlet entity service."""
    await hass.services.async_call(
        DOMAIN,
        SERVICE_CYCLE_OUTLET,
        {ATTR_ENTITY_ID: _entity_id(hass, 2)},
        blocking=True,
    )
    mock_controller.cycle_outlet.assert_awaited_once_with(2)


async def test_cycle_all_outlets_service(
    hass: HomeAssistant, setup_entry, mock_controller: MagicMock
) -> None:
    """Test the cycle_all_outlets entity service."""
    await hass.services.async_call(
        DOMAIN,
        SERVICE_CYCLE_ALL_OUTLETS,
        {ATTR_ENTITY_ID: _entity_id(hass, 1)},
        blocking=True,
    )
    mock_controller.cycle_all_outlets.assert_awaited_once()


async def test_set_outlet_name_service(
    hass: HomeAssistant, setup_entry, mock_controller: MagicMock
) -> None:
    """Test the set_outlet_name entity service."""
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_OUTLET_NAME,
        {ATTR_ENTITY_ID: _entity_id(hass, 1), CONF_NAME: "NAS"},
        blocking=True,
    )
    mock_controller.set_outlet_name.assert_awaited_once_with(1, "NAS")


async def test_set_pdu_name_service(
    hass: HomeAssistant, setup_entry, mock_controller: MagicMock
) -> None:
    """Test the set_pdu_name entity service."""
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_PDU_NAME,
        {ATTR_ENTITY_ID: _entity_id(hass, 1), CONF_NAME: "Rack PDU"},
        blocking=True,
    )
    mock_controller.set_pdu_name.assert_awaited_once_with("Rack PDU")


async def test_unavailable_when_disconnected(
    hass: HomeAssistant, setup_entry, mock_controller: MagicMock
) -> None:
    """Test switches become unavailable when the controller disconnects."""
    entity_id = _entity_id(hass, 1)
    assert hass.states.get(entity_id).state == STATE_ON

    mock_controller.connected = False
    mock_controller.update.return_value = False
    coordinator = setup_entry.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == "unavailable"
