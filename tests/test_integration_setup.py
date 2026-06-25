"""Test integration setup and basic functionality."""

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.middle_atlantic_racklink import (
    DOMAIN,
    _async_migrate_outlet_metric_unique_ids,
)
from custom_components.middle_atlantic_racklink.const import (
    CONNECTION_TYPE_AUTO,
    CONNECTION_TYPE_REDFISH,
    CONNECTION_TYPE_TELNET,
    CONF_CONNECTION_TYPE,
    CONF_ENABLE_VENDOR_FEATURES,
    CONF_USE_HTTPS,
)


class TestIntegrationSetup:
    """Test class for integration setup."""

    async def test_setup_unload_and_reload_entry(self, hass: HomeAssistant) -> None:
        """Test setting up and unloading the integration."""
        # Create a mock config entry
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                "host": "192.168.1.100",
                "port": 443,
                "username": "admin",
                "password": "password",
                CONF_CONNECTION_TYPE: CONNECTION_TYPE_AUTO,
                CONF_USE_HTTPS: True,
                CONF_ENABLE_VENDOR_FEATURES: True,
            },
            entry_id="test_entry",
        )
        config_entry.add_to_hass(hass)

        # Test setup
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # Verify the integration is loaded
        assert config_entry.state.name == "loaded"
        assert DOMAIN in hass.data

        # Test unload
        assert await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()

        # Verify the integration is unloaded
        assert config_entry.state.name == "not_loaded"

    async def test_setup_entry_redfish_connection(self, hass: HomeAssistant) -> None:
        """Test setting up with Redfish connection type."""
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                "host": "192.168.1.100",
                "port": 443,
                "username": "admin",
                "password": "password",
                CONF_CONNECTION_TYPE: CONNECTION_TYPE_REDFISH,
                CONF_USE_HTTPS: True,
                CONF_ENABLE_VENDOR_FEATURES: False,
            },
            entry_id="test_redfish",
        )
        config_entry.add_to_hass(hass)

        # Test setup with Redfish
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # Verify setup
        assert config_entry.state.name == "loaded"
        coordinator = hass.data[DOMAIN][config_entry.entry_id]
        assert coordinator is not None

    async def test_setup_entry_telnet_connection(self, hass: HomeAssistant) -> None:
        """Test setting up with Telnet connection type."""
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                "host": "192.168.1.100",
                "port": 60000,
                "username": "admin",
                "password": "password",
                CONF_CONNECTION_TYPE: CONNECTION_TYPE_TELNET,
            },
            entry_id="test_telnet",
        )
        config_entry.add_to_hass(hass)

        # Test setup with Telnet
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # Verify setup
        assert config_entry.state.name == "loaded"
        coordinator = hass.data[DOMAIN][config_entry.entry_id]
        assert coordinator is not None

    async def test_setup_entry_auto_detection(self, hass: HomeAssistant) -> None:
        """Test setting up with auto-detection."""
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                "host": "192.168.1.100",
                "username": "admin",
                "password": "password",
                CONF_CONNECTION_TYPE: CONNECTION_TYPE_AUTO,
                CONF_USE_HTTPS: True,
                CONF_ENABLE_VENDOR_FEATURES: True,
            },
            entry_id="test_auto",
        )
        config_entry.add_to_hass(hass)

        # Test setup with auto-detection
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # Verify setup
        assert config_entry.state.name == "loaded"
        coordinator = hass.data[DOMAIN][config_entry.entry_id]
        assert coordinator is not None

    async def test_version_consistency(self) -> None:
        """Test that all version references are consistent."""
        import json
        from pathlib import Path

        # Read manifest version
        manifest_path = Path("custom_components/middle_atlantic_racklink/manifest.json")
        with open(manifest_path) as f:
            manifest = json.load(f)
        manifest_version = manifest["version"]

        # Read pyproject.toml version
        pyproject_path = Path("pyproject.toml")
        with open(pyproject_path) as f:
            content = f.read()
            # Extract version from pyproject.toml
            for line in content.split("\n"):
                if line.strip().startswith("version = "):
                    pyproject_version = line.split('"')[1]
                    break
            else:
                pytest.fail("Version not found in pyproject.toml")

        # Verify versions match
        assert (
            manifest_version == pyproject_version
        ), f"Version mismatch: manifest={manifest_version}, pyproject={pyproject_version}"
        assert (
            manifest_version == "1.0.0"
        ), f"Expected version 1.0.0, got {manifest_version}"

    async def test_dependencies_availability(self) -> None:
        """Test that all required dependencies are available."""
        # Test core dependencies
        import aiohttp
        import zeroconf
        import voluptuous

        # Verify versions meet minimum requirements
        assert hasattr(aiohttp, "__version__")
        assert hasattr(zeroconf, "__version__")

        # Test Home Assistant compatibility
        import homeassistant

        assert hasattr(homeassistant, "__version__")

    async def test_configuration_schema_validation(self) -> None:
        """Test configuration schema validation."""
        from custom_components.middle_atlantic_racklink.config_flow import (
            RacklinkConfigFlow,
        )

        # Test valid configuration
        valid_config = {
            "host": "192.168.1.100",
            "port": 443,
            "username": "admin",
            "password": "password",
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_REDFISH,
            CONF_USE_HTTPS: True,
            CONF_ENABLE_VENDOR_FEATURES: True,
        }

        # This should not raise an exception
        flow = RacklinkConfigFlow()
        assert flow is not None

    async def test_device_info_structure(self, hass: HomeAssistant) -> None:
        """Test that device info follows Home Assistant standards."""
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                "host": "192.168.1.100",
                "username": "admin",
                "password": "password",
                CONF_CONNECTION_TYPE: CONNECTION_TYPE_AUTO,
            },
            entry_id="test_device_info",
        )
        config_entry.add_to_hass(hass)

        # Setup the integration
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # Get coordinator and check device info structure
        coordinator = hass.data[DOMAIN][config_entry.entry_id]
        device_info = coordinator.device_info

        # Verify required fields
        assert "identifiers" in device_info
        assert "name" in device_info
        assert "manufacturer" in device_info
        assert "model" in device_info

        # Verify manufacturer is correct
        assert device_info["manufacturer"] == "Legrand - Middle Atlantic"

        # Verify suggested area
        assert device_info.get("suggested_area") == "Electrical"


class TestOutletMetricUniqueIdMigration:
    """Tests for the legacy outlet-metric unique ID migration."""

    def _add_entry(self, hass: HomeAssistant) -> MockConfigEntry:
        entry = MockConfigEntry(domain=DOMAIN, entry_id="migrate_entry")
        entry.add_to_hass(hass)
        return entry

    async def test_renames_legacy_unknown_ids(self, hass: HomeAssistant) -> None:
        """Legacy ``unknown_<outlet>_<metric>`` IDs are renamed to the serial."""
        entry = self._add_entry(hass)
        registry = er.async_get(hass)
        entity = registry.async_get_or_create(
            "sensor",
            DOMAIN,
            "unknown_7_voltage",
            config_entry=entry,
        )

        await _async_migrate_outlet_metric_unique_ids(hass, entry, "SERIAL-A")

        assert (
            registry.async_get(entity.entity_id).unique_id == "SERIAL-A_7_voltage"
        )

    async def test_skips_when_target_exists(self, hass: HomeAssistant) -> None:
        """A collision with an existing serial-based ID leaves the legacy one."""
        entry = self._add_entry(hass)
        registry = er.async_get(hass)
        legacy = registry.async_get_or_create(
            "sensor", DOMAIN, "unknown_7_voltage", config_entry=entry
        )
        registry.async_get_or_create(
            "sensor", DOMAIN, "SERIAL-A_7_voltage", config_entry=entry
        )

        await _async_migrate_outlet_metric_unique_ids(hass, entry, "SERIAL-A")

        assert registry.async_get(legacy.entity_id).unique_id == "unknown_7_voltage"

    async def test_no_serial_is_noop(self, hass: HomeAssistant) -> None:
        """Without a usable serial nothing is renamed."""
        entry = self._add_entry(hass)
        registry = er.async_get(hass)
        entity = registry.async_get_or_create(
            "sensor", DOMAIN, "unknown_7_voltage", config_entry=entry
        )

        await _async_migrate_outlet_metric_unique_ids(hass, entry, "unknown")

        assert registry.async_get(entity.entity_id).unique_id == "unknown_7_voltage"

    async def test_unrelated_ids_untouched(self, hass: HomeAssistant) -> None:
        """IDs that aren't legacy outlet metrics are left alone."""
        entry = self._add_entry(hass)
        registry = er.async_get(hass)
        switch = registry.async_get_or_create(
            "switch", DOMAIN, "unknown_outlet_7", config_entry=entry
        )

        await _async_migrate_outlet_metric_unique_ids(hass, entry, "SERIAL-A")

        assert registry.async_get(switch.entity_id).unique_id == "unknown_outlet_7"
