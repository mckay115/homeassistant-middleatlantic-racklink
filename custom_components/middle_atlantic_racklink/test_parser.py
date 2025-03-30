"""
Test module for the Middle Atlantic Racklink parser.

This file can be used to manually test the parsing functions.
It is not part of the Home Assistant component, but a development tool.
"""

import logging
import os
import sys
import json
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.DEBUG)
_LOGGER = logging.getLogger("test_parser")

# Add the parent directory to the path so we can import the parser
current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir))

from parser import (
    parse_device_info,
    parse_network_info,
    parse_all_outlet_states,
    parse_outlet_names,
    parse_outlet_state,
    parse_outlet_details,
    parse_pdu_power_data,
    parse_pdu_temperature,
    normalize_model_name,
)


def test_parse_device_info():
    """Test parsing device information."""
    sample = """
    show pdu details
    PDU 'LiskoLabs Rack'
    Model:            RLNK-P920R
    Firmware Version: 2.2.0.1-51126
    Serial Number:    RLNKP-920_050a82
    Board Revision:   0x10
    """

    result = parse_device_info(sample)
    _LOGGER.info("Device info: %s", json.dumps(result, indent=2))

    assert result.get("name") == "LiskoLabs Rack", "Failed to parse PDU name"
    assert result.get("model") == "RLNK-P920R", "Failed to parse model"
    assert result.get("firmware") == "2.2.0.1-51126", "Failed to parse firmware"
    assert result.get("serial") == "RLNKP-920_050a82", "Failed to parse serial"


def test_parse_outlet_state():
    """Test parsing outlet state."""
    # Standard format
    sample1 = """
    show outlets 8 details
    Outlet 8 - Internal Rack Power:
    Power state: On
    """

    state1 = parse_outlet_state(sample1, 8)
    _LOGGER.info("Outlet state (standard): %s", state1)
    assert state1 is True, "Failed to parse outlet state (standard)"

    # Alternative format
    sample2 = """
    show outlets 8 details
    Outlet 8 - Internal Rack Power:
    State: On
    """

    state2 = parse_outlet_state(sample2, 8)
    _LOGGER.info("Outlet state (alternative): %s", state2)
    assert state2 is True, "Failed to parse outlet state (alternative)"

    # Off state
    sample3 = """
    show outlets 8 details
    Outlet 8 - Internal Rack Power:
    Power state: Off
    """

    state3 = parse_outlet_state(sample3, 8)
    _LOGGER.info("Outlet state (off): %s", state3)
    assert state3 is False, "Failed to parse outlet state (off)"


def test_parse_all_outlet_states():
    """Test parsing all outlet states."""
    sample = """
    show outlets all
    Outlet 1 - Firewall:
    Power state: On

    Outlet 2 - HP Switch:
    Power state: On

    Outlet 3 - Hades Canyon:
    Power state: On

    Outlet 4 - Automation NUC:
    Power state: On

    Outlet 5 - HomeCore:
    Power state: On

    Outlet 6 - MarsMedia??:
    Power state: On

    Outlet 7 - NAS:
    Power state: On

    Outlet 8 - Internal Rack Power:
    Power state: Off
    """

    result = parse_all_outlet_states(sample)
    _LOGGER.info("All outlet states: %s", json.dumps(result, indent=2))

    assert len(result) == 8, "Failed to parse all outlets"
    assert result.get(1) is True, "Failed to parse outlet 1 state"
    assert result.get(8) is False, "Failed to parse outlet 8 state"


def test_parse_outlet_details():
    """Test parsing outlet details."""
    sample = """
    show outlets 8 details
    Outlet 8 - Internal Rack Power:
    Power state: On

    RMS Current:        0.114 A (normal)
    RMS Voltage:        122.1 V (normal)
    Line Frequency:     60.0 Hz (normal)
    Active Power:       7 W (normal)
    Apparent Power:     14 VA (normal)
    Power Factor:       0.53 (normal)
    Active Energy:      632210 Wh (normal)
    """

    result = parse_outlet_details(sample, 8)
    _LOGGER.info("Outlet details: %s", json.dumps(result, indent=2))

    assert result.get("outlet_number") == 8, "Failed to parse outlet number"
    assert result.get("state") == "on", "Failed to parse outlet state"
    assert result.get("current") == 0.114, "Failed to parse current"
    assert result.get("voltage") == 122.1, "Failed to parse voltage"
    assert result.get("power") == 7, "Failed to parse power"
    assert result.get("apparent_power") == 14, "Failed to parse apparent power"
    assert result.get("power_factor") == 0.53, "Failed to parse power factor"
    assert result.get("energy") == 632210, "Failed to parse energy"
    assert result.get("line_frequency") == 60.0, "Failed to parse line frequency"


def test_normalize_model_name():
    """Test normalizing model names."""
    test_cases = [
        ("RLNK", "RLNK-P920R"),
        ("RACKLINK", "RLNK-P920R"),
        ("RLNK-P920R", "RLNK-P920R"),
        ("RLNKP920", "RLNK-P920"),
        ("P920", "RLNK-P920"),
        ("P915", "RLNK-P915"),
        ("UNKNOWN", "RLNK-UNKNOWN"),
    ]

    for input_name, expected in test_cases:
        result = normalize_model_name(input_name)
        _LOGGER.info("Normalized '%s' to '%s'", input_name, result)
        assert (
            result == expected
        ), f"Failed to normalize {input_name} -> {expected}, got {result}"


def run_tests():
    """Run all tests."""
    _LOGGER.info("Starting parser tests")

    test_parse_device_info()
    test_parse_outlet_state()
    test_parse_all_outlet_states()
    test_parse_outlet_details()
    test_normalize_model_name()

    _LOGGER.info("All tests passed!")


if __name__ == "__main__":
    run_tests()
