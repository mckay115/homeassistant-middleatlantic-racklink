"""Test the switches."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.switch import SwitchDeviceClass

from custom_components.middle_atlantic_racklink.switch import RacklinkOutlet


@pytest.fixture
def controller():
    """Create a mock controller."""
    with patch(
        "custom_components.middle_atlantic_racklink.switch.RacklinkController"
    ) as mock:
        controller = mock.return_value
        controller.async_get_outlet_state = AsyncMock()
        controller.async_set_outlet_state = AsyncMock()
        yield controller


@pytest.mark.asyncio
async def test_outlet_switch_on(controller):
    """Test outlet switch turn on."""
    controller.async_get_outlet_state.return_value = True
    controller.async_set_outlet_state.return_value = True

    switch = RacklinkOutlet(controller, "test_pdu", "test_outlet")
    await switch.async_turn_on()

    assert switch.is_on is True
    controller.async_set_outlet_state.assert_called_once_with(
        "test_pdu", "test_outlet", True
    )


@pytest.mark.asyncio
async def test_outlet_switch_off(controller):
    """Test outlet switch turn off."""
    controller.async_get_outlet_state.return_value = False
    controller.async_set_outlet_state.return_value = True

    switch = RacklinkOutlet(controller, "test_pdu", "test_outlet")
    await switch.async_turn_off()

    assert switch.is_on is False
    controller.async_set_outlet_state.assert_called_once_with(
        "test_pdu", "test_outlet", False
    )


@pytest.mark.asyncio
async def test_outlet_switch_update(controller):
    """Test outlet switch update."""
    controller.async_get_outlet_state.return_value = True

    switch = RacklinkOutlet(controller, "test_pdu", "test_outlet")
    await switch.async_update()

    assert switch.is_on is True
    assert switch.device_class == SwitchDeviceClass.OUTLET


@pytest.mark.asyncio
async def test_outlet_switch_error_handling(controller):
    """Test outlet switch error handling."""
    controller.async_get_outlet_state.side_effect = ValueError("Test error")
    controller.async_set_outlet_state.side_effect = ValueError("Test error")

    switch = RacklinkOutlet(controller, "test_pdu", "test_outlet")
    await switch.async_update()

    assert switch.is_on is None
    assert switch.available is False

    with pytest.raises(ValueError):
        await switch.async_turn_on()


@pytest.mark.asyncio
async def test_outlet_switch_cycle(controller):
    """Test outlet switch cycle."""
    controller.async_get_outlet_state.return_value = True
    controller.async_set_outlet_state.return_value = True

    switch = RacklinkOutlet(controller, "test_pdu", "test_outlet")
    await switch.async_cycle()

    assert controller.async_set_outlet_state.call_count == 2
    controller.async_set_outlet_state.assert_any_call("test_pdu", "test_outlet", False)
    controller.async_set_outlet_state.assert_any_call("test_pdu", "test_outlet", True)
