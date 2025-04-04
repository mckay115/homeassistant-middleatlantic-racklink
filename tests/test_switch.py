"""Test for the Middle Atlantic RackLink switch platform."""

from custom_components.middle_atlantic_racklink.coordinator import RacklinkCoordinator
from custom_components.middle_atlantic_racklink.switch import RacklinkOutletSwitch
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def coordinator():
    """Create a mock coordinator."""
    mock = MagicMock(spec=RacklinkCoordinator)
    mock.async_request_refresh = AsyncMock()
    mock.turn_outlet_on = AsyncMock()
    mock.turn_outlet_off = AsyncMock()
    mock.outlet_data = {1: {"state": False, "name": "Test Outlet"}}
    mock.available = True
    mock.controller = MagicMock()
    mock.controller.pdu_serial = "123456"
    mock.device_info = {
        "identifiers": {("middle_atlantic_racklink", "123456")},
        "name": "Test PDU",
        "manufacturer": "Middle Atlantic",
        "model": "Test Model",
    }
    return mock


@pytest.mark.asyncio
async def test_outlet_switch_on(coordinator):
    """Test outlet switch turn on."""
    switch = RacklinkOutletSwitch(coordinator, 1)
    await switch.async_turn_on()
    coordinator.turn_outlet_on.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_outlet_switch_off(coordinator):
    """Test outlet switch turn off."""
    switch = RacklinkOutletSwitch(coordinator, 1)
    await switch.async_turn_off()
    coordinator.turn_outlet_off.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_outlet_switch_update(coordinator):
    """Test outlet switch update."""
    switch = RacklinkOutletSwitch(coordinator, 1)
    await switch.async_update()
    coordinator.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_outlet_switch_error_handling(coordinator):
    """Test outlet switch error handling."""
    coordinator.turn_outlet_on.side_effect = ValueError("Test error")
    switch = RacklinkOutletSwitch(coordinator, 1)
    with pytest.raises(ValueError):
        await switch.async_turn_on()


@pytest.mark.asyncio
async def test_outlet_switch_toggle(coordinator):
    """Test outlet switch toggle."""
    switch = RacklinkOutletSwitch(coordinator, 1)
    await switch.async_toggle()
    coordinator.turn_outlet_on.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_outlet_switch_properties(coordinator):
    """Test outlet switch properties."""
    switch = RacklinkOutletSwitch(coordinator, 1)

    assert switch.name == "Outlet 1: Test Outlet"
    assert not switch.is_on
    assert switch.available
    assert switch.unique_id == "123456_outlet_1"
    assert switch.device_info == coordinator.device_info
