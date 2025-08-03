"""Test version consistency and validation."""

import json
import pytest
from pathlib import Path


class TestVersionValidation:
    """Test version consistency across all files."""

    def test_version_consistency(self):
        """Test that version is consistent across all configuration files."""
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

        # Verify current version is 1.0.0
        assert (
            manifest_version == "1.0.0"
        ), f"Expected version 1.0.0, got {manifest_version}"

    def test_manifest_structure(self):
        """Test that manifest.json has all required fields."""
        manifest_path = Path("custom_components/middle_atlantic_racklink/manifest.json")
        with open(manifest_path) as f:
            manifest = json.load(f)

        # Required fields
        required_fields = [
            "domain",
            "name",
            "version",
            "config_flow",
            "documentation",
            "issue_tracker",
            "requirements",
            "codeowners",
        ]

        for field in required_fields:
            assert field in manifest, f"Missing required field: {field}"

        # Verify specific values
        assert manifest["domain"] == "middle_atlantic_racklink"
        assert manifest["name"] == "Middle Atlantic RackLink"
        assert manifest["config_flow"] is True
        assert manifest["integration_type"] == "device"
        assert "aiohttp" in str(manifest["requirements"])
        assert "zeroconf" in str(manifest["requirements"])

    def test_hacs_config(self):
        """Test HACS configuration."""
        hacs_path = Path("hacs.json")
        with open(hacs_path) as f:
            hacs_config = json.load(f)

        # Required fields for HACS
        required_fields = [
            "name",
            "domains",
            "iot_class",
            "homeassistant",
        ]

        for field in required_fields:
            assert field in hacs_config, f"Missing required HACS field: {field}"

        # Verify specific values
        assert hacs_config["name"] == "Middle Atlantic RackLink PDU"
        assert "switch" in hacs_config["domains"]
        assert "sensor" in hacs_config["domains"]
        assert "binary_sensor" in hacs_config["domains"]
        assert "button" in hacs_config["domains"]
        assert hacs_config["iot_class"] == "local_polling"
        assert hacs_config["integration_type"] == "device"

    def test_minimum_ha_version(self):
        """Test minimum Home Assistant version consistency."""
        # Read from HACS config
        hacs_path = Path("hacs.json")
        with open(hacs_path) as f:
            hacs_config = json.load(f)
        hacs_min_version = hacs_config["homeassistant"]

        # Should be at least 2023.1.0 for modern features
        assert (
            hacs_min_version >= "2023.1.0"
        ), f"Minimum HA version too old: {hacs_min_version}"

    def test_pyproject_toml_structure(self):
        """Test pyproject.toml has proper structure."""
        pyproject_path = Path("pyproject.toml")
        assert pyproject_path.exists(), "pyproject.toml not found"

        with open(pyproject_path) as f:
            content = f.read()

        # Check for required sections
        required_sections = [
            "[project]",
            "[tool.black]",
            "[tool.isort]",
            "[tool.pytest.ini_options]",
            "[tool.mypy]",
            "[tool.coverage.run]",
        ]

        for section in required_sections:
            assert section in content, f"Missing section in pyproject.toml: {section}"

        # Check dependencies are listed
        assert "aiohttp" in content
        assert "zeroconf" in content
        assert "voluptuous" in content

    def test_changelog_exists(self):
        """Test that changelog exists and has proper structure."""
        changelog_path = Path("CHANGELOG.md")
        assert changelog_path.exists(), "CHANGELOG.md not found"

        with open(changelog_path) as f:
            content = f.read()

        # Check for version 1.0.0
        assert "[1.0.0]" in content, "Version 1.0.0 not found in changelog"

        # Check for required sections
        assert "## Added" in content or "### Added" in content
        assert "Redfish" in content, "Redfish features not mentioned in changelog"
        assert (
            "power monitoring" in content.lower()
        ), "Power monitoring not mentioned in changelog"

    def test_dependencies_version_constraints(self):
        """Test that dependencies have proper version constraints."""
        manifest_path = Path("custom_components/middle_atlantic_racklink/manifest.json")
        with open(manifest_path) as f:
            manifest = json.load(f)

        requirements = manifest["requirements"]

        # Check that versions are specified
        for req in requirements:
            if "aiohttp" in req:
                assert (
                    ">=" in req
                ), f"aiohttp should have minimum version constraint: {req}"
            if "zeroconf" in req:
                assert (
                    ">=" in req
                ), f"zeroconf should have minimum version constraint: {req}"

    def test_semantic_versioning(self):
        """Test that version follows semantic versioning."""
        manifest_path = Path("custom_components/middle_atlantic_racklink/manifest.json")
        with open(manifest_path) as f:
            manifest = json.load(f)

        version = manifest["version"]

        # Check format: MAJOR.MINOR.PATCH
        parts = version.split(".")
        assert (
            len(parts) == 3
        ), f"Version should have 3 parts (MAJOR.MINOR.PATCH): {version}"

        # Check that all parts are numbers
        for part in parts:
            assert part.isdigit(), f"Version part should be numeric: {part}"

        # For 1.0.0, verify it's a major release
        if version == "1.0.0":
            assert parts == ["1", "0", "0"], "Version 1.0.0 should be exactly 1.0.0"
