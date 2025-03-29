"""Test the RackLink controller."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.middle_atlantic_racklink.racklink_controller import (
    RacklinkController,
)


@pytest.fixture
def controller(hass: HomeAssistant):
    """Create a RackLink controller."""
    return RacklinkController(
        hass,
        "test_host",
        23,
        "test_user",
        "test_pass",
    )


@pytest.mark.asyncio
async def test_connect_success(controller):
    """Test successful connection."""
    with patch("telnetlib3.Telnet") as mock_telnet:
        mock_telnet.return_value.read_until.return_value = "Username:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "Password:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "#"

        await controller.connect()

        assert controller.available is True
        assert controller.connected is True
        assert controller.error is None


@pytest.mark.asyncio
async def test_connect_failure(controller):
    """Test connection failure."""
    with patch("telnetlib3.Telnet") as mock_telnet:
        mock_telnet.return_value.read_until.side_effect = ConnectionError(
            "Connection failed"
        )

        with pytest.raises(ConfigEntryNotReady):
            await controller.connect()

        assert controller.available is False
        assert controller.connected is False
        assert controller.error == "Connection failed"


@pytest.mark.asyncio
async def test_send_command(controller):
    """Test sending a command."""
    with patch("telnetlib3.Telnet") as mock_telnet:
        mock_telnet.return_value.read_until.return_value = "Username:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "Password:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "#"

        await controller.connect()

        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "test_command#"

        response = await controller.send_command("test_command")

        assert response == "test_command#"
        mock_telnet.return_value.write.assert_called_once_with("test_command\r\n")


@pytest.mark.asyncio
async def test_get_initial_status(controller):
    """Test getting initial status."""
    with patch("telnetlib3.Telnet") as mock_telnet:
        mock_telnet.return_value.read_until.return_value = "Username:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "Password:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "#"

        await controller.connect()

        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "show pdu details#"

        await controller.get_initial_status()

        assert mock_telnet.return_value.write.call_count == 3
        mock_telnet.return_value.write.assert_any_call("show pdu details\r\n")
        mock_telnet.return_value.write.assert_any_call("show outlets all details\r\n")
        mock_telnet.return_value.write.assert_any_call("show inlets all details\r\n")


@pytest.mark.asyncio
async def test_get_pdu_details(controller):
    """Test getting PDU details."""
    with patch("telnetlib3.Telnet") as mock_telnet:
        mock_telnet.return_value.read_until.return_value = "Username:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "Password:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "#"

        await controller.connect()

        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "show pdu details#"

        await controller.get_pdu_details()

        assert mock_telnet.return_value.write.call_count == 1
        mock_telnet.return_value.write.assert_called_once_with("show pdu details\r\n")


@pytest.mark.asyncio
async def test_get_outlet_details(controller):
    """Test getting outlet details."""
    with patch("telnetlib3.Telnet") as mock_telnet:
        mock_telnet.return_value.read_until.return_value = "Username:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "Password:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "#"

        await controller.connect()

        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "show outlets all details#"

        await controller.get_outlet_details()

        assert mock_telnet.return_value.write.call_count == 1
        mock_telnet.return_value.write.assert_called_once_with(
            "show outlets all details\r\n"
        )


@pytest.mark.asyncio
async def test_get_inlet_details(controller):
    """Test getting inlet details."""
    with patch("telnetlib3.Telnet") as mock_telnet:
        mock_telnet.return_value.read_until.return_value = "Username:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "Password:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "#"

        await controller.connect()

        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "show inlets all details#"

        await controller.get_inlet_details()

        assert mock_telnet.return_value.write.call_count == 1
        mock_telnet.return_value.write.assert_called_once_with(
            "show inlets all details\r\n"
        )


@pytest.mark.asyncio
async def test_set_outlet_state(controller):
    """Test setting outlet state."""
    with patch("telnetlib3.Telnet") as mock_telnet:
        mock_telnet.return_value.read_until.return_value = "Username:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "Password:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "#"

        await controller.connect()

        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "power outlets 1 on /y#"

        await controller.set_outlet_state(1, True)

        assert mock_telnet.return_value.write.call_count == 1
        mock_telnet.return_value.write.assert_called_once_with(
            "power outlets 1 on /y\r\n"
        )


@pytest.mark.asyncio
async def test_cycle_outlet(controller):
    """Test cycling an outlet."""
    with patch("telnetlib3.Telnet") as mock_telnet:
        mock_telnet.return_value.read_until.return_value = "Username:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "Password:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "#"

        await controller.connect()

        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "power outlets 1 cycle /y#"

        await controller.cycle_outlet(1)

        assert mock_telnet.return_value.write.call_count == 1
        mock_telnet.return_value.write.assert_called_once_with(
            "power outlets 1 cycle /y\r\n"
        )


@pytest.mark.asyncio
async def test_set_all_outlets_state(controller):
    """Test setting all outlets state."""
    with patch("telnetlib3.Telnet") as mock_telnet:
        mock_telnet.return_value.read_until.return_value = "Username:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "Password:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "#"

        await controller.connect()

        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "power outlets all on /y#"

        await controller.set_all_outlets_state(True)

        assert mock_telnet.return_value.write.call_count == 1
        mock_telnet.return_value.write.assert_called_once_with(
            "power outlets all on /y\r\n"
        )


@pytest.mark.asyncio
async def test_cycle_all_outlets(controller):
    """Test cycling all outlets."""
    with patch("telnetlib3.Telnet") as mock_telnet:
        mock_telnet.return_value.read_until.return_value = "Username:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "Password:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "#"

        await controller.connect()

        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "power outlets all cycle /y#"

        await controller.cycle_all_outlets()

        assert mock_telnet.return_value.write.call_count == 1
        mock_telnet.return_value.write.assert_called_once_with(
            "power outlets all cycle /y\r\n"
        )


@pytest.mark.asyncio
async def test_set_outlet_name(controller):
    """Test setting outlet name."""
    with patch("telnetlib3.Telnet") as mock_telnet:
        mock_telnet.return_value.read_until.return_value = "Username:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "Password:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "#"

        await controller.connect()

        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "outlet 1 name test_outlet#"

        await controller.set_outlet_name(1, "test_outlet")

        assert mock_telnet.return_value.write.call_count == 1
        mock_telnet.return_value.write.assert_called_once_with(
            "outlet 1 name test_outlet\r\n"
        )


@pytest.mark.asyncio
async def test_set_pdu_name(controller):
    """Test setting PDU name."""
    with patch("telnetlib3.Telnet") as mock_telnet:
        mock_telnet.return_value.read_until.return_value = "Username:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "Password:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "#"

        await controller.connect()

        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "pdu name test_pdu#"

        await controller.set_pdu_name("test_pdu")

        assert mock_telnet.return_value.write.call_count == 1
        mock_telnet.return_value.write.assert_called_once_with("pdu name test_pdu\r\n")


@pytest.mark.asyncio
async def test_disconnect(controller):
    """Test disconnecting."""
    with patch("telnetlib3.Telnet") as mock_telnet:
        mock_telnet.return_value.read_until.return_value = "Username:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "Password:"
        mock_telnet.return_value.write.return_value = True
        mock_telnet.return_value.read_until.return_value = "#"

        await controller.connect()

        await controller.disconnect()

        assert controller.available is False
        assert controller.connected is False
        assert controller.error is None
        mock_telnet.return_value.close.assert_called_once()
