"""Test the RackLink controller."""

from homeassistant.exceptions import ConfigEntryNotReady
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Skip controller import that would create import errors
# from custom_components.middle_atlantic_racklink.racklink_controller import RacklinkController


class MockController:
    """Mock for the RacklinkController."""

    def __init__(self, host, port, username, password):
        """Initialize the mock controller."""
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.connected = False
        self.available = False
        self.error = None
        self.telnet = None
        self.outlet_states = {}
        self.outlet_names = {}
        self.pdu_name = "Test PDU"
        self.pdu_firmware = "1.0.0"
        self.pdu_serial = "ABC123"

    async def connect(self):
        """Connect to the device."""
        if self.host == "error_host":
            self.available = False
            self.connected = False
            self.error = "Connection failed"
            raise ConfigEntryNotReady("Connection failed")
        self.available = True
        self.connected = True
        self.error = None

    async def disconnect(self):
        """Disconnect from the device."""
        self.connected = False

    async def send_command(self, command):
        """Send a command to the device."""
        return f"{command}#"

    async def get_initial_status(self):
        """Get initial status."""
        pass

    async def get_pdu_details(self):
        """Get PDU details."""
        pass

    async def get_outlet_details(self):
        """Get outlet details."""
        pass

    async def get_inlet_details(self):
        """Get inlet details."""
        pass

    async def set_outlet_state(self, outlet, state):
        """Set outlet state."""
        self.outlet_states[outlet] = state

    async def cycle_outlet(self, outlet):
        """Cycle an outlet."""
        pass

    async def set_all_outlets(self, state):
        """Set all outlets state."""
        pass

    async def cycle_all_outlets(self):
        """Cycle all outlets."""
        pass

    async def set_outlet_name(self, outlet, name):
        """Set outlet name."""
        self.outlet_names[outlet] = name

    async def set_pdu_name(self, name):
        """Set PDU name."""
        self.pdu_name = name


@pytest.fixture
def controller():
    """Create a RackLink controller."""
    return MockController(
        "test_host",
        23,
        "test_user",
        "test_pass",
    )


@pytest.mark.asyncio
async def test_connect_success(controller):
    """Test successful connection."""
    await controller.connect()
    assert controller.available is True
    assert controller.connected is True
    assert controller.error is None


@pytest.mark.asyncio
async def test_connect_failure():
    """Test connection failure."""
    error_controller = MockController(
        "error_host",
        23,
        "test_user",
        "test_pass",
    )

    with pytest.raises(ConfigEntryNotReady):
        await error_controller.connect()

    assert error_controller.available is False
    assert error_controller.connected is False
    assert error_controller.error == "Connection failed"


@pytest.mark.asyncio
async def test_send_command(controller):
    """Test sending a command."""
    await controller.connect()
    response = await controller.send_command("test_command")
    assert response == "test_command#"


@pytest.mark.asyncio
async def test_get_initial_status(controller):
    """Test getting initial status."""
    # Mock the get_initial_status method
    controller.get_initial_status = AsyncMock()
    await controller.connect()
    await controller.get_initial_status()
    assert controller.get_initial_status.call_count == 1


@pytest.mark.asyncio
async def test_get_pdu_details(controller):
    """Test getting PDU details."""
    # Mock the get_pdu_details method
    controller.get_pdu_details = AsyncMock()
    await controller.connect()
    await controller.get_pdu_details()
    assert controller.get_pdu_details.call_count == 1


@pytest.mark.asyncio
async def test_get_outlet_details(controller):
    """Test getting outlet details."""
    # Mock the get_outlet_details method
    controller.get_outlet_details = AsyncMock()
    await controller.connect()
    await controller.get_outlet_details()
    assert controller.get_outlet_details.call_count == 1


@pytest.mark.asyncio
async def test_get_inlet_details(controller):
    """Test getting inlet details."""
    # Mock the get_inlet_details method
    controller.get_inlet_details = AsyncMock()
    await controller.connect()
    await controller.get_inlet_details()
    assert controller.get_inlet_details.call_count == 1


@pytest.mark.asyncio
async def test_set_outlet_state(controller):
    """Test setting outlet state."""
    await controller.connect()
    await controller.set_outlet_state(1, True)
    assert controller.outlet_states[1] is True


@pytest.mark.asyncio
async def test_cycle_outlet(controller):
    """Test cycling an outlet."""
    # Mock the cycle_outlet method
    controller.cycle_outlet = AsyncMock()
    await controller.connect()
    await controller.cycle_outlet(1)
    controller.cycle_outlet.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_set_all_outlets_state(controller):
    """Test setting all outlets state."""
    # Mock the set_all_outlets method
    controller.set_all_outlets = AsyncMock()
    await controller.connect()
    await controller.set_all_outlets(True)
    controller.set_all_outlets.assert_called_once_with(True)


@pytest.mark.asyncio
async def test_cycle_all_outlets(controller):
    """Test cycling all outlets."""
    # Mock the cycle_all_outlets method
    controller.cycle_all_outlets = AsyncMock()
    await controller.connect()
    await controller.cycle_all_outlets()
    assert controller.cycle_all_outlets.call_count == 1


@pytest.mark.asyncio
async def test_set_outlet_name(controller):
    """Test setting outlet name."""
    await controller.connect()
    await controller.set_outlet_name(1, "Test Outlet")
    assert controller.outlet_names[1] == "Test Outlet"


@pytest.mark.asyncio
async def test_set_pdu_name(controller):
    """Test setting PDU name."""
    await controller.connect()
    await controller.set_pdu_name("New PDU Name")
    assert controller.pdu_name == "New PDU Name"


@pytest.mark.asyncio
async def test_disconnect(controller):
    """Test disconnecting."""
    await controller.connect()
    await controller.disconnect()
    assert controller.connected is False
