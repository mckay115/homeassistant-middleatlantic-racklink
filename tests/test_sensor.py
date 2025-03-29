"""Test the sensors."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
)

from custom_components.middle_atlantic_racklink.sensor import (
    RacklinkCurrent,
    RacklinkFrequency,
    RacklinkPower,
    RacklinkPowerFactor,
    RacklinkTemperature,
    RacklinkVoltage,
)


@pytest.fixture
def controller():
    """Create a mock controller."""
    with patch(
        "custom_components.middle_atlantic_racklink.sensor.RacklinkController"
    ) as mock:
        controller = mock.return_value
        controller.async_get_current = AsyncMock()
        controller.async_get_frequency = AsyncMock()
        controller.async_get_power_factor = AsyncMock()
        controller.async_get_power = AsyncMock()
        controller.async_get_temperature = AsyncMock()
        controller.async_get_voltage = AsyncMock()
        controller.sensors = {}
        yield controller


@pytest.mark.asyncio
async def test_current_sensor(controller):
    """Test current sensor."""
    controller.sensors["current"] = 10.5

    sensor = RacklinkCurrent(controller)
    await sensor.async_update()

    assert sensor.state == 10.5
    assert sensor.device_class == SensorDeviceClass.CURRENT
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.unit_of_measurement == "A"


@pytest.mark.asyncio
async def test_frequency_sensor(controller):
    """Test frequency sensor."""
    controller.sensors["frequency"] = 60.0

    sensor = RacklinkFrequency(controller)
    await sensor.async_update()

    assert sensor.state == 60.0
    assert sensor.device_class == SensorDeviceClass.FREQUENCY
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.unit_of_measurement == "Hz"


@pytest.mark.asyncio
async def test_power_factor_sensor(controller):
    """Test power factor sensor."""
    controller.sensors["power_factor"] = 0.95

    sensor = RacklinkPowerFactor(controller)
    await sensor.async_update()

    assert sensor.state == 0.95
    assert sensor.device_class == SensorDeviceClass.POWER_FACTOR
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.unit_of_measurement == "%"


@pytest.mark.asyncio
async def test_power_sensor(controller):
    """Test power sensor."""
    controller.sensors["power"] = 1200.0

    sensor = RacklinkPower(controller)
    await sensor.async_update()

    assert sensor.state == 1200.0
    assert sensor.device_class == SensorDeviceClass.POWER
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.unit_of_measurement == "W"


@pytest.mark.asyncio
async def test_temperature_sensor(controller):
    """Test temperature sensor."""
    controller.sensors["temperature"] = 25.5

    sensor = RacklinkTemperature(controller)
    await sensor.async_update()

    assert sensor.state == 25.5
    assert sensor.device_class == SensorDeviceClass.TEMPERATURE
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.unit_of_measurement == "°C"


@pytest.mark.asyncio
async def test_voltage_sensor(controller):
    """Test voltage sensor."""
    controller.sensors["voltage"] = 120.0

    sensor = RacklinkVoltage(controller)
    await sensor.async_update()

    assert sensor.state == 120.0
    assert sensor.device_class == SensorDeviceClass.VOLTAGE
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.unit_of_measurement == "V"


@pytest.mark.asyncio
async def test_sensor_error_handling(controller):
    """Test sensor error handling."""
    # Instead of using ValueError as a value, we'll modify the test approach
    controller.sensors = {}  # No sensor data

    sensor = RacklinkCurrent(controller)
    await sensor.async_update()

    # If no sensor data is available, the state should be None
    assert sensor.state is None
