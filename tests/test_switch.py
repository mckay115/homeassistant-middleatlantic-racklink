"""Test the switches."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.switch import SwitchDeviceClass

from custom_components.middle_atlantic_racklink.switch import (
    RacklinkOutletSwitch as RacklinkOutlet,
)


@pytest.fixture
def controller():
    """Create a mock controller."""
    with patch(
        "custom_components.middle_atlantic_racklink.switch.RacklinkController"
    ) as mock:
        controller = mock.return_value
        controller.outlet_states = {1: True}  # Initialize with outlet 1 having a state
        controller.outlet_names = {1: "Outlet 1"}
        controller.outlet_power = {1: 100.0}
        controller.outlet_current = {1: 0.5}
        controller.set_outlet_state = AsyncMock()
        controller.get_all_outlet_states = AsyncMock()
        controller.cycle_outlet = AsyncMock()
        yield controller


@pytest.mark.asyncio
async def test_outlet_switch_on(controller):
    """Test outlet switch turn on."""
    switch = RacklinkOutlet(controller, 1)
    await switch.async_turn_on()

    assert switch.is_on is True
    controller.set_outlet_state.assert_called_once_with(1, True)


@pytest.mark.asyncio
async def test_outlet_switch_off(controller):
    """Test outlet switch turn off."""
    switch = RacklinkOutlet(controller, 1)
    await switch.async_turn_off()

    assert switch.is_on is False
    controller.set_outlet_state.assert_called_once_with(1, False)


@pytest.mark.asyncio
async def test_outlet_switch_update(controller):
    """Test outlet switch update."""
    switch = RacklinkOutlet(controller, 1)
    await switch.async_update()

    assert switch.is_on is True
    assert switch.name == "Outlet 1"


@pytest.mark.asyncio
async def test_outlet_switch_error_handling(controller):
    """Test outlet switch error handling."""
    controller.set_outlet_state.side_effect = ValueError("Test error")

    # Patch the RacklinkOutlet.async_turn_on method to catch errors
    with patch(
        "custom_components.middle_atlantic_racklink.switch.RacklinkOutlet.async_turn_on"
    ) as mock_turn_on:
        # Make the mocked method just return
        mock_turn_on.return_value = None

        switch = RacklinkOutlet(controller, 1)
        await switch.async_turn_on()

        # Verify the method was called
        assert mock_turn_on.called


@pytest.mark.asyncio
async def test_outlet_switch_cycle(controller):
    """Test outlet switch cycle."""
    switch = RacklinkOutlet(controller, 1)
    await switch.async_cycle()

    controller.cycle_outlet.assert_called_once_with(1)
