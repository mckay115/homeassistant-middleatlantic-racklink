"""Test the Middle Atlantic RackLink button platform."""

from __future__ import annotations

from .conftest import MOCK_PDU_INFO
from custom_components.middle_atlantic_racklink.const import DOMAIN
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import (
    SERVICE_PRESS,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from unittest.mock import MagicMock

SERIAL = MOCK_PDU_INFO["pdu_serial"]


def _entity_id(hass: HomeAssistant, unique_id: str) -> str:
    """Return the entity ID of a button by unique ID."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("button", DOMAIN, unique_id)
    assert entity_id is not None, f"No button registered for {unique_id}"
    return entity_id


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        BUTTON_DOMAIN,
        SERVICE_PRESS,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )


async def test_outlet_cycle_button(
    hass: HomeAssistant, mock_config_entry, mock_controller: MagicMock
) -> None:
    """Test pressing an outlet cycle button cycles the outlet."""
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    await _press(hass, _entity_id(hass, f"{SERIAL}_outlet_1_cycle"))
    mock_controller.cycle_outlet.assert_awaited_once_with(1)


async def test_cycle_all_outlets_button(
    hass: HomeAssistant, mock_config_entry, mock_controller: MagicMock
) -> None:
    """Test pressing the cycle-all button cycles every outlet."""
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    await _press(hass, _entity_id(hass, f"{SERIAL}_all_outlets_cycle"))
    mock_controller.cycle_all_outlets.assert_awaited_once()


async def test_vendor_feature_buttons(
    hass: HomeAssistant, mock_config_entry, mock_controller: MagicMock
) -> None:
    """Test load shedding and sequence buttons call the controller."""
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    await _press(hass, _entity_id(hass, f"{SERIAL}_start_load_shedding"))
    mock_controller.start_load_shedding.assert_awaited_once()

    await _press(hass, _entity_id(hass, f"{SERIAL}_stop_sequence"))
    mock_controller.stop_sequence.assert_awaited_once()


async def test_vendor_buttons_absent_without_telnet(
    hass: HomeAssistant, mock_config_entry, mock_controller: MagicMock
) -> None:
    """Test vendor-feature buttons are not created without a telnet channel."""
    mock_controller.has_vendor_features = False

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("button", DOMAIN, f"{SERIAL}_start_load_shedding")
        is None
    )
    # The plain cycle-all button is always present
    assert (
        registry.async_get_entity_id("button", DOMAIN, f"{SERIAL}_all_outlets_cycle")
        is not None
    )
