"""Test the Middle Atlantic RackLink number platform."""

from __future__ import annotations

from .conftest import MOCK_PDU_INFO
from custom_components.middle_atlantic_racklink.const import (
    CONF_SEQUENCE_DELAY,
    DOMAIN,
)
from homeassistant.components.number import (
    ATTR_VALUE,
)
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.number import (
    SERVICE_SET_VALUE,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from unittest.mock import MagicMock

SERIAL = MOCK_PDU_INFO["pdu_serial"]


def _entity_id(hass: HomeAssistant) -> str:
    """Return the entity ID of the sequence delay number."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "number", DOMAIN, f"{SERIAL}_sequence_delay"
    )
    assert entity_id is not None
    return entity_id


async def test_sequence_delay_default(
    hass: HomeAssistant, mock_config_entry, mock_controller: MagicMock
) -> None:
    """Test the sequence delay defaults to 2 seconds."""
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(_entity_id(hass)).state == "2"


async def test_set_sequence_delay_persists_and_applies(
    hass: HomeAssistant, mock_config_entry, mock_controller: MagicMock
) -> None:
    """Test setting the delay stores it in options and uses it for sequencing."""
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: _entity_id(hass), ATTR_VALUE: 5},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert mock_config_entry.options[CONF_SEQUENCE_DELAY] == 5
    assert hass.states.get(_entity_id(hass)).state == "5"

    # Starting the sequence now uses the configured delay
    registry = er.async_get(hass)
    sequence_switch = registry.async_get_entity_id(
        "switch", DOMAIN, f"{SERIAL}_sequence"
    )
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: sequence_switch},
        blocking=True,
    )
    mock_controller.start_sequence.assert_awaited_once_with(5)


async def test_number_absent_without_telnet(
    hass: HomeAssistant, mock_config_entry, mock_controller: MagicMock
) -> None:
    """Test the sequence delay number is not created without a telnet channel."""
    mock_controller.has_vendor_features = False

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("number", DOMAIN, f"{SERIAL}_sequence_delay")
        is None
    )
