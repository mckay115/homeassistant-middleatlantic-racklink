"""Test the binary sensors."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from custom_components.middle_atlantic_racklink.binary_sensor import RacklinkOutletState


@pytest.fixture
def controller():
    """Create a mock controller."""
    with patch(
        "custom_components.middle_atlantic_racklink.binary_sensor.RacklinkController"
    ) as mock:
        controller = mock.return_value
        controller.async_get_outlet_state = AsyncMock()
        yield controller


@pytest.mark.asyncio
async def test_outlet_state_on(controller):
    """Test outlet state on."""
    controller.async_get_outlet_state.return_value = True

    sensor = RacklinkOutletState(controller, "test_pdu", "test_outlet")
    await sensor.async_update()

    assert sensor.is_on is True
    assert sensor.device_class == BinarySensorDeviceClass.POWER
    assert sensor.name == "test_pdu test_outlet state"


@pytest.mark.asyncio
async def test_outlet_state_off(controller):
    """Test outlet state off."""
    controller.async_get_outlet_state.return_value = False

    sensor = RacklinkOutletState(controller, "test_pdu", "test_outlet")
    await sensor.async_update()

    assert sensor.is_on is False
    assert sensor.device_class == BinarySensorDeviceClass.POWER
    assert sensor.name == "test_pdu test_outlet state"


@pytest.mark.asyncio
async def test_outlet_state_error_handling(controller):
    """Test outlet state error handling."""
    controller.async_get_outlet_state.side_effect = ValueError("Test error")

    sensor = RacklinkOutletState(controller, "test_pdu", "test_outlet")
    await sensor.async_update()

    assert sensor.is_on is None
    assert sensor.available is False
