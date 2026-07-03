"""Diagnostics support for the Middle Atlantic RackLink integration."""

from __future__ import annotations

from typing import Any, Dict

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import RacklinkConfigEntry

TO_REDACT = {CONF_HOST, CONF_PASSWORD, CONF_USERNAME, "mac_address"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: RacklinkConfigEntry
) -> Dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    controller = coordinator.controller

    return {
        "config_entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "device": {
            "model": controller.pdu_model,
            "firmware": controller.pdu_firmware,
            "connection_type": controller.connection_type,
            "connected": controller.connected,
            "available": controller.available,
            "hybrid_vendor_features": controller.has_vendor_features,
            "outlet_count": len(controller.outlet_states),
            "per_outlet_metrics_available": controller.per_outlet_metrics_available,
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "data": coordinator.data,
        },
    }
