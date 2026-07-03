"""Test the Middle Atlantic RackLink sensor platform."""

from __future__ import annotations

from .conftest import MOCK_PDU_INFO
from custom_components.middle_atlantic_racklink.const import DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from unittest.mock import MagicMock

SERIAL = MOCK_PDU_INFO["pdu_serial"]


def _get_state(hass: HomeAssistant, unique_id: str):
    """Return the state object for a sensor by its unique ID."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
    assert entity_id is not None, f"No sensor registered for {unique_id}"
    return hass.states.get(entity_id)


async def test_pdu_sensors(
    hass: HomeAssistant, mock_config_entry, mock_controller: MagicMock
) -> None:
    """Test PDU-level sensor values."""
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert _get_state(hass, f"{SERIAL}_voltage").state == "120.0"
    assert _get_state(hass, f"{SERIAL}_current").state == "2.5"
    assert _get_state(hass, f"{SERIAL}_power").state == "300.0"
    assert _get_state(hass, f"{SERIAL}_frequency").state == "60.0"
    assert _get_state(hass, f"{SERIAL}_apparent_power").state == "320.0"
    assert _get_state(hass, f"{SERIAL}_power_factor").state == "0.94"
    # Energy is stored internally in Wh and exposed in kWh
    assert _get_state(hass, f"{SERIAL}_energy").state == "1.5"


async def test_outlet_sensors(
    hass: HomeAssistant, mock_config_entry, mock_controller: MagicMock
) -> None:
    """Test per-outlet sensor values, including legitimate zero readings."""
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert _get_state(hass, f"{SERIAL}_outlet_1_power").state == "50.0"
    assert _get_state(hass, f"{SERIAL}_outlet_1_energy").state == "2.0"
    assert _get_state(hass, f"{SERIAL}_outlet_1_current").state == "0.4"
    assert _get_state(hass, f"{SERIAL}_outlet_1_voltage").state == "120.0"
    # A 0.0 reading is a real value, not "unknown"
    assert _get_state(hass, f"{SERIAL}_outlet_2_power").state == "0.0"


async def test_missing_values_are_unknown(
    hass: HomeAssistant, mock_config_entry, mock_controller: MagicMock
) -> None:
    """Test sensors report unknown instead of fabricated values."""
    mock_controller.power_factor = None
    mock_controller.apparent_power = None
    mock_controller.active_energy = None

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert _get_state(hass, f"{SERIAL}_power_factor").state == "unknown"
    assert _get_state(hass, f"{SERIAL}_apparent_power").state == "unknown"
    assert _get_state(hass, f"{SERIAL}_energy").state == "unknown"


async def test_legacy_unique_id_migration(
    hass: HomeAssistant, mock_config_entry, mock_controller: MagicMock
) -> None:
    """Test broken legacy unknown_* unique IDs are migrated to the serial."""
    registry = er.async_get(hass)
    legacy = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "unknown_1_power",
        config_entry=mock_config_entry,
    )

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    migrated = registry.async_get(legacy.entity_id)
    assert migrated is not None
    assert migrated.unique_id == f"{SERIAL}_outlet_1_power"


async def test_new_outlets_add_sensors(
    hass: HomeAssistant, mock_config_entry, mock_controller: MagicMock
) -> None:
    """Test sensors appear for outlets discovered after setup."""
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, f"{SERIAL}_outlet_3_power")
        is None
    )

    mock_controller.outlet_states[3] = True
    mock_controller.outlet_names[3] = "Outlet 3"
    mock_controller.outlet_power_data[3] = 10.0

    coordinator = mock_config_entry.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert (
        registry.async_get_entity_id("sensor", DOMAIN, f"{SERIAL}_outlet_3_power")
        is not None
    )
