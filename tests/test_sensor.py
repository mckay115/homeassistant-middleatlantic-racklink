"""Test the sensors."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from custom_components.middle_atlantic_racklink.sensor import (
    RacklinkCurrent,
    RacklinkFrequency,
    RacklinkPowerFactor,
    RacklinkPower,
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
        yield controller


@pytest.mark.asyncio
async def test_current_sensor(controller):
    """Test current sensor."""
    controller.async_get_current.return_value = 10.5

    sensor = RacklinkCurrent(controller, "test_pdu", "test_outlet")
    await sensor.async_update()

    assert sensor.native_value == 10.5
    assert sensor.device_class == SensorDeviceClass.CURRENT
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.native_unit_of_measurement == "A"


@pytest.mark.asyncio
async def test_frequency_sensor(controller):
    """Test frequency sensor."""
    controller.async_get_frequency.return_value = 60.0

    sensor = RacklinkFrequency(controller, "test_pdu", "test_outlet")
    await sensor.async_update()

    assert sensor.native_value == 60.0
    assert sensor.device_class == SensorDeviceClass.FREQUENCY
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.native_unit_of_measurement == "Hz"


@pytest.mark.asyncio
async def test_power_factor_sensor(controller):
    """Test power factor sensor."""
    controller.async_get_power_factor.return_value = 0.95

    sensor = RacklinkPowerFactor(controller, "test_pdu", "test_outlet")
    await sensor.async_update()

    assert sensor.native_value == 0.95
    assert sensor.device_class == SensorDeviceClass.POWER_FACTOR
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.native_unit_of_measurement is None


@pytest.mark.asyncio
async def test_power_sensor(controller):
    """Test power sensor."""
    controller.async_get_power.return_value = 1200.0

    sensor = RacklinkPower(controller, "test_pdu", "test_outlet")
    await sensor.async_update()

    assert sensor.native_value == 1200.0
    assert sensor.device_class == SensorDeviceClass.POWER
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.native_unit_of_measurement == "W"


@pytest.mark.asyncio
async def test_temperature_sensor(controller):
    """Test temperature sensor."""
    controller.async_get_temperature.return_value = 25.5

    sensor = RacklinkTemperature(controller, "test_pdu", "test_outlet")
    await sensor.async_update()

    assert sensor.native_value == 25.5
    assert sensor.device_class == SensorDeviceClass.TEMPERATURE
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.native_unit_of_measurement == "°C"


@pytest.mark.asyncio
async def test_voltage_sensor(controller):
    """Test voltage sensor."""
    controller.async_get_voltage.return_value = 120.0

    sensor = RacklinkVoltage(controller, "test_pdu", "test_outlet")
    await sensor.async_update()

    assert sensor.native_value == 120.0
    assert sensor.device_class == SensorDeviceClass.VOLTAGE
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.native_unit_of_measurement == "V"


@pytest.mark.asyncio
async def test_sensor_error_handling(controller):
    """Test sensor error handling."""
    controller.async_get_current.side_effect = ValueError("Test error")

    sensor = RacklinkCurrent(controller, "test_pdu", "test_outlet")
    await sensor.async_update()

    assert sensor.native_value is None
    assert sensor.available is False
