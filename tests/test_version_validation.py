"""Test packaging metadata consistency."""

from __future__ import annotations

from pathlib import Path

import json

import pytest

MANIFEST_PATH = Path("custom_components/middle_atlantic_racklink/manifest.json")
HACS_PATH = Path("hacs.json")
PYPROJECT_PATH = Path("pyproject.toml")

# Keys accepted by HACS for integration repositories
VALID_HACS_KEYS = {
    "name",
    "content_in_root",
    "filename",
    "country",
    "homeassistant",
    "hacs",
    "persistent_directory",
    "render_readme",
    "zip_release",
    "hide_default_branch",
}


def _load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def _pyproject_version() -> str:
    for line in PYPROJECT_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("version = "):
            return line.split('"')[1]
    pytest.fail("Version not found in pyproject.toml")


def test_manifest_version_matches_pyproject() -> None:
    """Test the manifest and pyproject versions stay in sync."""
    assert _load_json(MANIFEST_PATH)["version"] == _pyproject_version()


def test_manifest_version_is_semver() -> None:
    """Test the version follows MAJOR.MINOR.PATCH."""
    version = _load_json(MANIFEST_PATH)["version"]
    parts = version.split(".")
    assert len(parts) == 3, f"Expected MAJOR.MINOR.PATCH, got {version}"
    assert all(part.isdigit() for part in parts)


def test_manifest_required_fields() -> None:
    """Test manifest.json has the fields hassfest requires."""
    manifest = _load_json(MANIFEST_PATH)

    for field in (
        "domain",
        "name",
        "version",
        "config_flow",
        "documentation",
        "issue_tracker",
        "requirements",
        "codeowners",
        "iot_class",
        "integration_type",
    ):
        assert field in manifest, f"Missing required field: {field}"

    assert manifest["domain"] == "middle_atlantic_racklink"
    assert manifest["config_flow"] is True
    assert manifest["integration_type"] == "device"
    assert manifest["iot_class"] == "local_polling"
    # zeroconf is provided by Home Assistant core, not a pip requirement
    assert manifest["requirements"] == []


def test_hacs_config_is_valid() -> None:
    """Test hacs.json only contains keys HACS understands."""
    hacs_config = _load_json(HACS_PATH)

    assert hacs_config["name"]
    unknown_keys = set(hacs_config) - VALID_HACS_KEYS
    assert not unknown_keys, f"Invalid hacs.json keys: {sorted(unknown_keys)}"


def test_minimum_ha_version() -> None:
    """Test the declared minimum Home Assistant version is modern."""
    hacs_config = _load_json(HACS_PATH)
    assert hacs_config["homeassistant"] >= "2024.1.0"
