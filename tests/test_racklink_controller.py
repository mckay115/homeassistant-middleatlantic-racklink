"""Test the real RacklinkController with mocked connections."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.middle_atlantic_racklink.const import (
    CONNECTION_TYPE_REDFISH,
    CONNECTION_TYPE_TELNET,
)
from custom_components.middle_atlantic_racklink.controller.racklink_controller import (
    RacklinkController,
)
from custom_components.middle_atlantic_racklink.exceptions import (
    RacklinkAuthenticationError,
)
from custom_components.middle_atlantic_racklink.redfish_connection import (
    RedfishConnection,
)
from custom_components.middle_atlantic_racklink.socket_connection import (
    SocketConnection,
)

CONTROLLER_MODULE = (
    "custom_components.middle_atlantic_racklink.controller.racklink_controller"
)


def make_redfish_connection() -> MagicMock:
    """Create a mocked Redfish connection that passes isinstance checks."""
    connection = MagicMock(spec=RedfishConnection)
    connection.connect = AsyncMock(return_value=True)
    connection.disconnect = AsyncMock()
    connection.config = MagicMock()
    connection.config.port = 443
    return connection


def make_socket_connection(protocol: str = "telnet") -> MagicMock:
    """Create a mocked socket connection that passes isinstance checks."""
    connection = MagicMock(spec=SocketConnection)
    connection.connect = AsyncMock(return_value=True)
    connection.disconnect = AsyncMock()
    connection.protocol_type = protocol
    connection.outlet_names = {}
    connection.config = MagicMock()
    connection.config.port = 6000
    return connection


def make_controller(connection_type: str = CONNECTION_TYPE_REDFISH):
    """Create a controller with vendor features disabled."""
    return RacklinkController(
        host="192.168.1.100",
        port=443,
        username="admin",
        password="secret",
        connection_type=connection_type,
        enable_vendor_features=False,
    )


@pytest.fixture
def no_sleep():
    """Skip the intentional pacing delays in the controller."""
    with patch(f"{CONTROLLER_MODULE}.asyncio.sleep", new=AsyncMock()):
        yield


async def test_connect_success() -> None:
    """Test connecting with an explicit connection type."""
    controller = make_controller()
    connection = make_redfish_connection()

    with patch(f"{CONTROLLER_MODULE}.ConnectionFactory") as factory:
        factory.create_connection.return_value = connection
        assert await controller.connect() is True

    assert controller.connected is True
    assert controller.available is True
    connection.connect.assert_awaited_once()


async def test_connect_failure() -> None:
    """Test a failed connection leaves the controller disconnected."""
    controller = make_controller()
    connection = make_redfish_connection()
    connection.connect.return_value = False

    with patch(f"{CONTROLLER_MODULE}.ConnectionFactory") as factory:
        factory.create_connection.return_value = connection
        assert await controller.connect() is False

    assert controller.connected is False
    assert controller.available is False


async def test_connect_auth_error_propagates() -> None:
    """Test authentication errors are raised as typed exceptions."""
    controller = make_controller()
    connection = make_redfish_connection()
    connection.connect.side_effect = RacklinkAuthenticationError("denied")

    with patch(f"{CONTROLLER_MODULE}.ConnectionFactory") as factory:
        factory.create_connection.return_value = connection
        with pytest.raises(RacklinkAuthenticationError):
            await controller.connect()

    assert controller.connected is False


async def test_reconnect_closes_previous_connection() -> None:
    """Test reconnecting closes the old connection (no session leak)."""
    controller = make_controller()
    first = make_redfish_connection()
    second = make_redfish_connection()

    with patch(f"{CONTROLLER_MODULE}.ConnectionFactory") as factory:
        factory.create_connection.side_effect = [first, second]
        assert await controller.connect() is True
        assert await controller.connect() is True

    first.disconnect.assert_awaited_once()
    assert controller.connection is second


async def test_disconnect_resets_state() -> None:
    """Test disconnect closes the connection and clears flags."""
    controller = make_controller()
    connection = make_redfish_connection()

    with patch(f"{CONTROLLER_MODULE}.ConnectionFactory") as factory:
        factory.create_connection.return_value = connection
        await controller.connect()

    await controller.disconnect()
    connection.disconnect.assert_awaited_once()
    assert controller.connected is False
    assert controller.connection is None


async def test_turn_outlet_on_redfish() -> None:
    """Test turning an outlet on via Redfish."""
    controller = make_controller()
    connection = make_redfish_connection()
    connection.set_outlet_state = AsyncMock(return_value=True)
    controller.connection = connection

    assert await controller.turn_outlet_on(3) is True
    connection.set_outlet_state.assert_awaited_once_with(3, True)
    assert controller.outlet_states[3] is True


async def test_turn_outlet_off_telnet() -> None:
    """Test turning an outlet off via the telnet protocol."""
    controller = make_controller(CONNECTION_TYPE_TELNET)
    connection = make_socket_connection("telnet")
    connection.telnet_outlet_command = AsyncMock(return_value=True)
    controller.connection = connection

    assert await controller.turn_outlet_off(2) is True
    connection.telnet_outlet_command.assert_awaited_once_with(2, "off")
    assert controller.outlet_states[2] is False


async def test_outlet_command_failure_returns_false() -> None:
    """Test a rejected outlet command does not update local state."""
    controller = make_controller()
    connection = make_redfish_connection()
    connection.set_outlet_state = AsyncMock(return_value=False)
    controller.connection = connection

    assert await controller.turn_outlet_on(1) is False
    assert 1 not in controller.outlet_states


async def test_cycle_all_outlets_reports_partial_failure(no_sleep) -> None:
    """Test cycle_all_outlets returns False when an outlet fails."""
    controller = make_controller(CONNECTION_TYPE_TELNET)
    connection = make_socket_connection("telnet")
    connection.send_command = AsyncMock(side_effect=["", "error: failed"])
    controller.connection = connection
    controller.outlet_states = {1: True, 2: True}

    assert await controller.cycle_all_outlets() is False


async def test_cycle_all_outlets_success(no_sleep) -> None:
    """Test cycle_all_outlets succeeds when all outlets are accepted."""
    controller = make_controller(CONNECTION_TYPE_TELNET)
    connection = make_socket_connection("telnet")
    connection.send_command = AsyncMock(return_value="")
    controller.connection = connection
    controller.outlet_states = {1: False, 2: False}

    assert await controller.cycle_all_outlets() is True
    assert controller.outlet_states == {1: True, 2: True}


async def test_redfish_energy_converted_to_wh() -> None:
    """Test Redfish kWh energy readings are stored as Wh."""
    controller = make_controller()
    connection = make_redfish_connection()
    connection.get_power_metrics = AsyncMock(
        return_value={"power": 300.0, "energy": 1.5, "power_factor": 0.94}
    )
    controller.connection = connection

    await controller._update_redfish_pdu_metrics()
    assert controller.active_energy == 1500.0
    assert controller.active_power == 300.0
    assert controller.power_factor == 0.94


async def test_redfish_metrics_preserve_previous_on_missing() -> None:
    """Test missing metric keys do not clobber previous readings."""
    controller = make_controller()
    controller.active_power = 250.0
    connection = make_redfish_connection()
    connection.get_power_metrics = AsyncMock(return_value={"energy": 2.0})
    controller.connection = connection

    await controller._update_redfish_pdu_metrics()
    assert controller.active_power == 250.0
    assert controller.active_energy == 2000.0


def test_parse_non_critical_outlets() -> None:
    """Test parsing of the loadshedding outlet list including ranges."""
    response = "Load shedding: Inactive\nNon Critical Outlets: 3, 6-8\n"
    assert RacklinkController._parse_non_critical_outlets(response) == {3, 6, 7, 8}


def test_parse_non_critical_outlets_missing() -> None:
    """Test a response without the outlet list returns None."""
    assert RacklinkController._parse_non_critical_outlets("Load shedding: Off") is None


async def test_set_outlet_name_redfish() -> None:
    """Test renaming an outlet via Redfish updates local state."""
    controller = make_controller()
    connection = make_redfish_connection()
    connection.set_outlet_label = AsyncMock(return_value=True)
    controller.connection = connection

    assert await controller.set_outlet_name(1, "NAS") is True
    connection.set_outlet_label.assert_awaited_once_with(1, "NAS")
    assert controller.outlet_names[1] == "NAS"


async def test_set_pdu_name_redfish() -> None:
    """Test renaming the PDU via Redfish updates local state."""
    controller = make_controller()
    connection = make_redfish_connection()
    connection.set_pdu_name = AsyncMock(return_value=True)
    controller.connection = connection

    assert await controller.set_pdu_name("Rack PDU") is True
    assert controller.pdu_name == "Rack PDU"


async def test_load_shedding_requires_telnet() -> None:
    """Test load shedding fails cleanly without a telnet channel."""
    controller = make_controller()
    controller.connection = make_redfish_connection()

    assert await controller.start_load_shedding() is False


async def test_load_shedding_via_telnet(no_sleep) -> None:
    """Test load shedding commands go through the telnet channel."""
    controller = make_controller(CONNECTION_TYPE_TELNET)
    connection = make_socket_connection("telnet")
    connection.send_command = AsyncMock(return_value="")
    controller.connection = connection
    controller.socket = connection

    assert await controller.start_load_shedding() is True
    assert controller.load_shedding_active is True
    connection.send_command.assert_any_await("loadshedding start /y")
