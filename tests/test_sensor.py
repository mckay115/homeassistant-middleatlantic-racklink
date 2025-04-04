"""Test for the Middle Atlantic RackLink sensor platform."""

from custom_components.middle_atlantic_racklink.sensor import (
    RacklinkCurrentSensor,
    RacklinkFrequencySensor,
    RacklinkPowerSensor,
    RacklinkVoltageSensor,
)
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def controller():
    """Create a mock controller."""
    mock = MagicMock()
    mock.async_request_refresh = AsyncMock()
    mock.system_data = MagicMock()
    mock.system_data.get = MagicMock()
    return mock


@pytest.mark.asyncio
async def test_current_sensor(controller):
    """Test current sensor."""
    controller.system_data.get.return_value = 10.5
    sensor = RacklinkCurrentSensor(controller)

    # Initial update
    await sensor.async_update()
    controller.async_request_refresh.assert_called_once()

    # Check state
    assert sensor.native_value == 10.5
    assert sensor.device_class == SensorDeviceClass.CURRENT
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.native_unit_of_measurement == "A"


@pytest.mark.asyncio
async def test_frequency_sensor(controller):
    """Test frequency sensor."""
    controller.system_data.get.return_value = 60.0
    sensor = RacklinkFrequencySensor(controller)

    # Initial update
    await sensor.async_update()
    controller.async_request_refresh.assert_called_once()

    # Check state
    assert sensor.native_value == 60.0
    assert sensor.device_class == SensorDeviceClass.FREQUENCY
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.native_unit_of_measurement == "Hz"


@pytest.mark.asyncio
async def test_power_sensor(controller):
    """Test power sensor."""
    controller.system_data.get.return_value = 1200.0
    sensor = RacklinkPowerSensor(controller)

    # Initial update
    await sensor.async_update()
    controller.async_request_refresh.assert_called_once()

    # Check state
    assert sensor.native_value == 1200.0
    assert sensor.device_class == SensorDeviceClass.POWER
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.native_unit_of_measurement == "W"


@pytest.mark.asyncio
async def test_voltage_sensor(controller):
    """Test voltage sensor."""
    controller.system_data.get.return_value = 120.0
    sensor = RacklinkVoltageSensor(controller)

    # Initial update
    await sensor.async_update()
    controller.async_request_refresh.assert_called_once()

    # Check state
    assert sensor.native_value == 120.0
    assert sensor.device_class == SensorDeviceClass.VOLTAGE
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.native_unit_of_measurement == "V"


@pytest.mark.asyncio
async def test_sensor_error_handling(controller):
    """Test sensor error handling."""
    controller.system_data.get.return_value = None
    sensor = RacklinkCurrentSensor(controller)

    # Initial update
    await sensor.async_update()
    controller.async_request_refresh.assert_called_once()

    # Check state
    assert sensor.native_value is None
