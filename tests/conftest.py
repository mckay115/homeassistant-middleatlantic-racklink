"""Configuration for pytest."""

import os
import sys
from pathlib import Path
import tempfile
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

# Add the repository root to the Python path if needed
ROOT_DIR = Path(__file__).parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from custom_components.middle_atlantic_racklink.const import DOMAIN


@pytest.fixture
def hass():
    """Return a mocked Home Assistant instance."""
    hass_mock = MagicMock()
    hass_mock.config_entries = MagicMock()
    hass_mock.states = MagicMock()
    hass_mock.config = MagicMock()
    hass_mock.config.components = set()
    hass_mock.data = {DOMAIN: {}}
    return hass_mock


@pytest.fixture
def mock_now():
    """Return a fixed datetime."""
    return "2023-01-01T12:00:00Z"


@pytest.fixture
def enable_custom_integrations(hass):
    """Enable custom integrations in Home Assistant."""
    hass.data.pop("custom_components", None)


@pytest.fixture
async def setup_integration(hass: HomeAssistant):
    """Set up the integration."""
    await async_setup_component(
        hass,
        DOMAIN,
        {
            DOMAIN: [
                {
                    "host": "test_host",
                    "port": 23,
                    "username": "test_user",
                    "password": "test_pass",
                }
            ]
        },
    )
    await hass.async_block_till_done()
