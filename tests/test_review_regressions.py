"""Regression coverage for setup, energy and PDU control review findings."""

from __future__ import annotations

from .conftest import build_controller_mock, MOCK_CONFIG, MOCK_PDU_INFO
from .test_racklink_controller import (
    make_controller,
    make_redfish_connection,
    make_socket_connection,
)
from custom_components.middle_atlantic_racklink.config_flow import (
    CannotConnect,
    validate_connection,
)
from custom_components.middle_atlantic_racklink.const import (
    CONF_SCAN_INTERVAL,
    CONF_SEQUENCE_DELAY,
    DOMAIN,
)
from custom_components.middle_atlantic_racklink.sensor import _wh_to_kwh
from custom_components.middle_atlantic_racklink.socket_connection import OUTLET_CYCLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from unittest.mock import AsyncMock, patch

import pytest


async def test_validation_rejects_failed_refresh(hass: HomeAssistant) -> None:
    controller = build_controller_mock()
    controller.update.return_value = False
    with patch(
        "custom_components.middle_atlantic_racklink.config_flow.RacklinkController",
        return_value=controller,
    ):
        with pytest.raises(CannotConnect):
            await validate_connection(hass, dict(MOCK_CONFIG))
    controller.disconnect.assert_awaited_once()


async def test_failed_setup_closes_connection(
    hass: HomeAssistant, mock_config_entry, mock_controller
) -> None:
    mock_controller.update.return_value = False
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert mock_controller.disconnect.await_count >= 1


async def test_options_preserve_sequence_delay(
    hass: HomeAssistant, mock_config_entry
) -> None:
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_SEQUENCE_DELAY: 17}
    )
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 42}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options[CONF_SEQUENCE_DELAY] == 17


async def test_duplicate_does_not_replace_credentials(
    hass: HomeAssistant, mock_config_entry
) -> None:
    original = dict(mock_config_entry.data)
    with (
        patch(
            "custom_components.middle_atlantic_racklink.config_flow.discover_racklink_devices",
            return_value=[],
        ),
        patch(
            "custom_components.middle_atlantic_racklink.config_flow.validate_connection",
            return_value=MOCK_PDU_INFO,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"host": "new-address", "username": "admin", "password": "other-password"},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"connection_type": "redfish"}
        )
    assert result["reason"] == "already_configured"
    assert dict(mock_config_entry.data) == original


async def test_binary_cycle_all_uses_binary_commands() -> None:
    controller = make_controller()
    connection = make_socket_connection("binary")
    connection.send_outlet_command = AsyncMock(return_value=True)
    controller.connection = connection
    controller.outlet_states = {1: False, 2: True}
    with patch(
        "custom_components.middle_atlantic_racklink.controller.racklink_controller.asyncio.sleep",
        new=AsyncMock(),
    ):
        assert await controller.cycle_all_outlets()
    assert connection.send_outlet_command.await_args_list[0].args == (
        1,
        OUTLET_CYCLE,
        5,
    )
    assert connection.send_outlet_command.await_count == 2


async def test_empty_outlet_response_fails_refresh() -> None:
    controller = make_controller()
    connection = make_redfish_connection()
    connection.get_all_outlets_info = AsyncMock(return_value={})
    controller.connection = connection
    controller.outlet_states = {1: True}
    with pytest.raises(ConnectionError):
        await controller._update_outlet_states()


async def test_missing_outlet_metrics_clear_stale_energy() -> None:
    controller = make_controller()
    connection = make_redfish_connection()
    connection.get_all_outlets_metrics = AsyncMock(return_value={})
    controller.connection = connection
    controller.outlet_energy_data = {1: 1000.0}
    await controller._update_redfish_outlet_data()
    assert not controller.outlet_energy_data


@pytest.mark.parametrize("reading", [-1.0, float("nan"), float("inf")])
def test_invalid_energy_does_not_enter_statistics(reading: float) -> None:
    assert _wh_to_kwh(reading) is None


async def test_registry_identity_stays_stable(
    hass: HomeAssistant, mock_config_entry, mock_controller
) -> None:
    """A delayed serial update must not detach entities from the original device."""
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    coordinator = mock_config_entry.runtime_data
    original = coordinator.device_info["identifiers"]
    mock_controller.pdu_serial = "different-delayed-serial"
    assert coordinator.device_info["identifiers"] == original
    assert mock_config_entry.data["pdu_serial"] == MOCK_PDU_INFO["pdu_serial"]


def test_surge_status_does_not_infer_from_generic_health() -> None:
    controller = make_controller()
    controller._parse_redfish_health({"Status": {"Health": "OK"}})
    assert controller.surge_protection_ok is None
    controller._parse_redfish_health({"SurgeProtected": False})
    assert controller.surge_protection_ok is False


async def test_energy_sensor_has_statistics_metadata(
    hass: HomeAssistant, mock_config_entry, mock_controller
) -> None:
    from homeassistant.helpers import entity_registry as er

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{MOCK_PDU_INFO['pdu_serial']}_energy"
    )
    state = hass.states.get(entity_id)
    assert state.attributes["device_class"] == "energy"
    assert state.attributes["state_class"] == "total_increasing"
    assert state.attributes["unit_of_measurement"] == "kWh"


async def test_outlet_auth_failure_is_not_swallowed() -> None:
    from custom_components.middle_atlantic_racklink.exceptions import (
        RacklinkAuthenticationError,
    )
    from custom_components.middle_atlantic_racklink.redfish_connection import (
        RedfishConfig,
        RedfishConnection,
    )

    connection = RedfishConnection(RedfishConfig(host="pdu.local"))
    connection._outlet_endpoints = {1: "/outlets/1"}
    with patch.object(
        connection,
        "_fetch_json",
        new=AsyncMock(side_effect=RacklinkAuthenticationError("denied")),
    ):
        with pytest.raises(RacklinkAuthenticationError):
            await connection.get_all_outlets_info()
