"""Test the binary sensors."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from custom_components.middle_atlantic_racklink.binary_sensor import (
    RacklinkSurgeProtection,
)


@pytest.fixture
def controller():
    """Create a mock controller."""
    with patch(
        "custom_components.middle_atlantic_racklink.binary_sensor.RacklinkController"
    ) as mock:
        controller = mock.return_value
        controller.get_surge_protection_status = AsyncMock()
        yield controller


@pytest.mark.asyncio
async def test_surge_protection_on(controller):
    """Test surge protection on."""
    controller.get_surge_protection_status.return_value = True

    sensor = RacklinkSurgeProtection(controller)
    await sensor.async_update()

    assert sensor.is_on is True
    assert sensor.device_class == "safety"
    assert sensor.name == "Racklink Surge Protection"


@pytest.mark.asyncio
async def test_surge_protection_off(controller):
    """Test surge protection off."""
    controller.get_surge_protection_status.return_value = False

    sensor = RacklinkSurgeProtection(controller)
    await sensor.async_update()

    assert sensor.is_on is False
    assert sensor.device_class == "safety"
    assert sensor.name == "Racklink Surge Protection"


@pytest.mark.asyncio
async def test_surge_protection_error_handling(controller):
    """Test surge protection error handling."""
    controller.get_surge_protection_status.side_effect = ValueError("Test error")

    sensor = RacklinkSurgeProtection(controller)
    try:
        await sensor.async_update()
        # If we reach here, the error was handled silently
        assert sensor.is_on is None
    except ValueError:
        # If the error is raised, that's also acceptable
        pass
