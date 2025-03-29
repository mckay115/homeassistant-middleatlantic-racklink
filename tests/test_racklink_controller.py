"""Test the RacklinkController class."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.middle_atlantic_racklink.racklink_controller import (
    RacklinkController,
)


@pytest.fixture
def controller():
    """Create a RacklinkController instance."""
    return RacklinkController("test_host", 23, "test_user", "test_pass")


@pytest.mark.asyncio
async def test_connect_success(controller):
    """Test successful connection."""
    with patch("telnetlib.Telnet") as mock_telnet:
        mock_telnet.return_value.read_until.side_effect = [
            b"Username:",
            b"Password:",
            b"#",
        ]
        await controller.connect()
        assert controller.connected
        assert controller.available
        assert controller.last_error is None


@pytest.mark.asyncio
async def test_connect_failure(controller):
    """Test connection failure."""
    with patch("telnetlib.Telnet") as mock_telnet:
        mock_telnet.side_effect = Exception("Connection failed")
        with pytest.raises(ValueError):
            await controller.connect()
        assert not controller.connected
        assert not controller.available
        assert controller.last_error is not None


@pytest.mark.asyncio
async def test_send_command(controller):
    """Test sending commands."""
    with patch("telnetlib.Telnet") as mock_telnet:
        mock_telnet.return_value.read_until.side_effect = [
            b"Username:",
            b"Password:",
            b"#",
            b"Response#",
        ]
        await controller.connect()
        response = await controller.send_command("test command")
        assert response == "Response"
        mock_telnet.return_value.write.assert_called_with(b"test command\r\n")


@pytest.mark.asyncio
async def test_get_pdu_details(controller):
    """Test getting PDU details."""
    mock_response = """
    Model: RLNK-P415
    Firmware Version: 1.2.3
    Serial Number: 123456
    """
    with patch.object(controller, "send_command", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = mock_response
        await controller.get_pdu_details()
        assert controller.pdu_model == "RLNK-P415"
        assert controller.pdu_firmware == "1.2.3"
        assert controller.pdu_serial == "123456"


@pytest.mark.asyncio
async def test_get_all_outlet_states(controller):
    """Test getting all outlet states."""
    mock_response = """
    Outlet 1:
    Name: Test Outlet
    Power state: On
    RMS Current: 1.5A
    Active Power: 100W
    Active Energy: 1000Wh
    Power Factor: 0.95%
    """
    with patch.object(controller, "send_command", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = mock_response
        await controller.get_all_outlet_states()
        assert controller.outlet_states[1] is True
        assert controller.outlet_names[1] == "Test Outlet"
        assert controller.outlet_power[1] == 100.0
        assert controller.outlet_current[1] == 1.5
        assert controller.outlet_energy[1] == 1000.0
        assert controller.outlet_power_factor[1] == 0.95


@pytest.mark.asyncio
async def test_set_outlet_state(controller):
    """Test setting outlet state."""
    with patch.object(controller, "send_command", new_callable=AsyncMock) as mock_send:
        await controller.set_outlet_state(1, True)
        mock_send.assert_called_with("power outlets 1 on /y")
        assert controller.outlet_states[1] is True


@pytest.mark.asyncio
async def test_cycle_outlet(controller):
    """Test cycling an outlet."""
    with patch.object(controller, "send_command", new_callable=AsyncMock) as mock_send:
        await controller.cycle_outlet(1)
        mock_send.assert_called_with("power outlets 1 cycle /y")


@pytest.mark.asyncio
async def test_set_outlet_name(controller):
    """Test setting outlet name."""
    with patch.object(controller, "send_command", new_callable=AsyncMock) as mock_send:
        await controller.set_outlet_name(1, "New Name")
        assert mock_send.call_count == 3
        mock_send.assert_any_call("config")
        mock_send.assert_any_call('outlet 1 name "New Name"')
        mock_send.assert_any_call("apply")
        assert controller.outlet_names[1] == "New Name"


@pytest.mark.asyncio
async def test_set_all_outlets(controller):
    """Test setting all outlets."""
    with patch.object(controller, "send_command", new_callable=AsyncMock) as mock_send:
        await controller.set_all_outlets(True)
        mock_send.assert_called_with("power outlets all on /y")
        for outlet in controller.outlet_states:
            assert controller.outlet_states[outlet] is True


@pytest.mark.asyncio
async def test_cycle_all_outlets(controller):
    """Test cycling all outlets."""
    with patch.object(controller, "send_command", new_callable=AsyncMock) as mock_send:
        await controller.cycle_all_outlets()
        mock_send.assert_called_with("power outlets all cycle /y")


@pytest.mark.asyncio
async def test_get_surge_protection_status(controller):
    """Test getting surge protection status."""
    mock_response = "Surge Protection: Active"
    with patch.object(controller, "send_command", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = mock_response
        status = await controller.get_surge_protection_status()
        assert status is True


@pytest.mark.asyncio
async def test_get_sensor_values(controller):
    """Test getting sensor values."""
    mock_response = """
    RMS Voltage: 120V
    RMS Current: 2.5A
    Active Power: 300W
    Frequency: 60Hz
    Power Factor: 0.98%
    Temperature: 25°C
    """
    with patch.object(controller, "send_command", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = mock_response
        await controller.get_sensor_values()
        assert controller.sensors["voltage"] == 120.0
        assert controller.sensors["current"] == 2.5
        assert controller.sensors["power"] == 300.0
        assert controller.sensors["frequency"] == 60.0
        assert controller.sensors["power_factor"] == 0.98
        assert controller.sensors["temperature"] == 25.0


@pytest.mark.asyncio
async def test_get_device_info(controller):
    """Test getting device info."""
    controller.pdu_name = "Test PDU"
    controller.pdu_model = "RLNK-P415"
    controller.pdu_firmware = "1.2.3"
    controller.pdu_serial = "123456"
    controller.pdu_mac = "00:11:22:33:44:55"
    controller._available = True
    controller._last_error = None
    controller._last_error_time = None
    controller._last_update = 1234567890

    info = await controller.get_device_info()
    assert info["name"] == "Test PDU"
    assert info["model"] == "RLNK-P415"
    assert info["firmware"] == "1.2.3"
    assert info["serial"] == "123456"
    assert info["mac"] == "00:11:22:33:44:55"
    assert info["available"] is True
    assert info["last_error"] is None
    assert info["last_error_time"] is None
    assert info["last_update"] is not None
