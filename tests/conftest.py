"""Common test fixtures."""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.middle_atlantic_racklink import DOMAIN


@pytest.fixture
async def hass() -> HomeAssistant:
    """Create a Home Assistant instance."""
    hass = HomeAssistant()
    await hass.async_start()
    yield hass
    await hass.async_stop()


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
