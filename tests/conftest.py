"""Shared fixtures for the Middle Atlantic RackLink tests."""

from __future__ import annotations

from custom_components.middle_atlantic_racklink.const import (
    CONF_CONNECTION_TYPE,
    CONNECTION_TYPE_REDFISH,
    DOMAIN,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

MOCK_CONFIG = {
    "host": "192.168.1.100",
    "port": 443,
    "username": "admin",
    "password": "secret",
    CONF_CONNECTION_TYPE: CONNECTION_TYPE_REDFISH,
    "use_https": True,
    "enable_vendor_features": True,
}

MOCK_PDU_INFO = {
    "pdu_name": "Test PDU",
    "pdu_model": "RLNK-P920R",
    "pdu_firmware": "2.2.0.1",
    "pdu_serial": "RLNKP-920_050a82",
    "mac_address": "00:1e:c5:05:0a:82",
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    """Enable loading custom integrations in all tests."""


def build_controller_mock() -> MagicMock:
    """Build a fully-populated RacklinkController mock."""
    controller = MagicMock()
    controller.connect = AsyncMock(return_value=True)
    controller.update = AsyncMock(return_value=True)
    controller.disconnect = AsyncMock()
    controller.turn_outlet_on = AsyncMock(return_value=True)
    controller.turn_outlet_off = AsyncMock(return_value=True)
    controller.cycle_outlet = AsyncMock(return_value=True)
    controller.cycle_all_outlets = AsyncMock(return_value=True)
    controller.start_load_shedding = AsyncMock(return_value=True)
    controller.stop_load_shedding = AsyncMock(return_value=True)
    controller.start_sequence = AsyncMock(return_value=True)
    controller.stop_sequence = AsyncMock(return_value=True)
    controller.set_outlet_name = AsyncMock(return_value=True)
    controller.set_pdu_name = AsyncMock(return_value=True)

    controller.connected = True
    controller.available = True
    controller.host = MOCK_CONFIG["host"]
    controller.connection_type = CONNECTION_TYPE_REDFISH
    controller.enable_vendor_features = True
    controller.has_vendor_features = True
    controller.per_outlet_metrics_available = True

    controller.pdu_name = MOCK_PDU_INFO["pdu_name"]
    controller.pdu_model = MOCK_PDU_INFO["pdu_model"]
    controller.pdu_firmware = MOCK_PDU_INFO["pdu_firmware"]
    controller.pdu_serial = MOCK_PDU_INFO["pdu_serial"]
    controller.mac_address = MOCK_PDU_INFO["mac_address"]

    controller.rms_voltage = 120.0
    controller.rms_current = 2.5
    controller.active_power = 300.0
    controller.active_energy = 1500.0  # Wh
    controller.line_frequency = 60.0
    controller.apparent_power = 320.0
    controller.power_factor = 0.94

    controller.outlet_states = {1: True, 2: False}
    controller.outlet_names = {1: "Firewall", 2: "Outlet 2"}
    controller.outlet_attrs = {}
    controller.outlet_power_data = {1: 50.0, 2: 0.0}
    controller.outlet_energy_data = {1: 2000.0, 2: 0.0}  # Wh
    controller.outlet_current_data = {1: 0.4, 2: 0.0}
    controller.outlet_voltage_data = {1: 120.0, 2: 120.0}
    controller.outlet_non_critical = {1: False, 2: True}

    controller.load_shedding_active = False
    controller.sequence_active = False
    controller.surge_protection_ok = True

    return controller


@pytest.fixture
def mock_controller() -> MagicMock:
    """Return a mocked RacklinkController patched into the integration setup."""
    controller = build_controller_mock()
    with patch(
        "custom_components.middle_atlantic_racklink.RacklinkController",
        return_value=controller,
    ):
        yield controller


@pytest.fixture
def mock_config_entry(hass) -> MockConfigEntry:
    """Return a config entry added to hass."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test PDU (RLNK-P920R)",
        data=MOCK_CONFIG,
        unique_id=MOCK_PDU_INFO["mac_address"],
    )
    entry.add_to_hass(hass)
    return entry
