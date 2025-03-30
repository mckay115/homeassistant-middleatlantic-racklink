"""Parser for Middle Atlantic Racklink responses."""

import logging
import re
from typing import Dict, Any, List, Tuple, Optional

_LOGGER = logging.getLogger(__name__)


def parse_device_info(response: str) -> Dict[str, Any]:
    """Parse PDU device information from 'show pdu details' response."""
    if not response:
        return {}

    result = {}

    # Parse PDU name - from two possible formats:
    # 1. PDU 'Name'
    # 2. Name: 'Name'
    name_match = re.search(
        r"PDU\s+'([^']+)'|Name:\s+'([^']+)'",
        response,
        re.IGNORECASE,
    )
    if name_match:
        result["name"] = name_match.group(1) or name_match.group(2)
        _LOGGER.debug("Found PDU name: %s", result["name"])

    # Parse model
    model_match = re.search(
        r"Model:\s*(.+?)(?:\r|\n)",
        response,
        re.IGNORECASE,
    )
    if model_match:
        result["model"] = model_match.group(1).strip()
        _LOGGER.debug("Found PDU model: %s", result["model"])

    # Parse serial number
    sn_match = re.search(
        r"Serial Number:\s*(.+?)(?:\r|\n)",
        response,
        re.IGNORECASE,
    )
    if sn_match:
        result["serial"] = sn_match.group(1).strip()
        _LOGGER.debug("Found PDU serial: %s", result["serial"])

    # Parse firmware version
    fw_match = re.search(
        r"Firmware Version:\s*(.+?)(?:\r|\n)",
        response,
        re.IGNORECASE,
    )
    if fw_match:
        result["firmware"] = fw_match.group(1).strip()
        _LOGGER.debug("Found PDU firmware: %s", result["firmware"])

    return result


def parse_network_info(response: str) -> Dict[str, Any]:
    """Parse network interface information from 'show network interface' response."""
    if not response:
        return {}

    result = {}

    # Parse MAC address
    mac_match = re.search(
        r"MAC address:\s*(.+?)(?:\r|\n)",
        response,
        re.IGNORECASE,
    )
    if mac_match:
        result["mac_address"] = mac_match.group(1).strip()
        _LOGGER.debug("Found PDU MAC address: %s", result["mac_address"])

    # Parse IP address
    ip_match = re.search(
        r"IPv4 address:\s*(.+?)(?:\r|\n)",
        response,
        re.IGNORECASE,
    )
    if ip_match:
        result["ip_address"] = ip_match.group(1).strip()

    return result


def parse_all_outlet_states(response: str) -> Dict[int, bool]:
    """Parse all outlet states from 'show outlets all' response."""
    if not response:
        _LOGGER.warning("Empty response received for parsing all outlet states")
        return {}

    # Log full response for debugging
    _LOGGER.debug("Full response for parse_all_outlet_states:\n%s", response)

    outlet_states = {}

    # Attempt different patterns for extracting outlet states
    # Pattern 1: Standard format with "Power state:"
    outlet_blocks = re.findall(
        r"Outlet (\d+)[^\n]*\n\s*Power state:\s*(\w+)",
        response,
        re.MULTILINE,
    )

    # Pattern 2: Using "Status:" instead of "Power state:"
    if not outlet_blocks:
        outlet_blocks = re.findall(
            r"Outlet (\d+)[^\n]*\n\s*Status:\s*(\w+)",
            response,
            re.MULTILINE,
        )

    # Pattern 3: More generic pattern looking for state/status
    if not outlet_blocks:
        outlet_blocks = re.findall(
            r"Outlet\s+(\d+).*?(?:state|status)[^:]*:\s*(\w+)",
            response,
            re.IGNORECASE | re.DOTALL,
        )

    # Pattern 4: Looking for direct "Outlet X is ON/OFF" format
    if not outlet_blocks:
        outlet_blocks = re.findall(
            r"Outlet\s+(\d+)\s+is\s+(ON|OFF|On|Off)",
            response,
            re.IGNORECASE,
        )

    # Pattern 5: Looking for "Outlet X ... ON/OFF" within proximity
    if not outlet_blocks:
        raw_blocks = re.findall(
            r"Outlet\s+(\d+).*?(?:\n.*?){0,5}(ON|OFF|On|Off)",
            response,
            re.IGNORECASE | re.DOTALL,
        )
        if raw_blocks:
            _LOGGER.debug("Found %d outlets using proximity pattern", len(raw_blocks))
            outlet_blocks = [(m[0], m[1]) for m in raw_blocks]

    # Pattern 6: Extreme fallback - just look for numbers followed by on/off
    if not outlet_blocks:
        raw_blocks = re.findall(
            r"(\d+)(?:[^a-zA-Z0-9]{1,20})(on|off)",
            response.lower(),
            re.DOTALL,
        )
        if raw_blocks:
            _LOGGER.debug("Found %d outlets using fallback pattern", len(raw_blocks))
            outlet_blocks = [(m[0], m[1]) for m in raw_blocks]

    # Log if we found any outlet blocks
    if outlet_blocks:
        _LOGGER.debug("Found %d outlet blocks in response", len(outlet_blocks))
    else:
        _LOGGER.warning(
            "No outlet blocks found in response - parser could not detect any outlets"
        )
        # Log a small excerpt to help diagnose the issue
        _LOGGER.debug(
            "First 200 chars of response: %s", response[:200].replace("\n", " ")
        )
        return {}

    # Process each found outlet
    for match in outlet_blocks:
        try:
            outlet_num = int(match[0])
            state_text = match[1].lower()
            # Consider "on", "active", "enabled" as ON states
            state = state_text in ("on", "active", "enabled", "true", "1")

            # Store the state
            outlet_states[outlet_num] = state

            _LOGGER.debug(
                "Found outlet %d state: %s (from text: '%s')",
                outlet_num,
                "ON" if state else "OFF",
                match[1],
            )
        except (ValueError, IndexError) as e:
            _LOGGER.error("Error parsing outlet block: %s - %s", match, e)

    # Log summary
    if outlet_states:
        _LOGGER.debug("Successfully parsed %d outlet states", len(outlet_states))
    else:
        _LOGGER.warning("No outlet states could be parsed from the response")

    return outlet_states


def parse_outlet_names(response: str) -> Dict[int, str]:
    """Parse outlet names from 'show outlets all' response."""
    if not response:
        return {}

    outlet_names = {}

    # Parse outlet names from headers
    # Format examples:
    # "Outlet 1 - Firewall:"
    # "Outlet 2:"
    outlet_blocks = re.findall(
        r"Outlet (\d+)(?:\s+-\s+([^:\r\n]+))?:",
        response,
        re.MULTILINE,
    )

    # Process each found outlet
    for match in outlet_blocks:
        try:
            outlet_num = int(match[0])
            outlet_name = (
                match[1].strip()
                if len(match) > 1 and match[1]
                else f"Outlet {outlet_num}"
            )

            # Store the name
            outlet_names[outlet_num] = outlet_name

            _LOGGER.debug("Found outlet %d name: %s", outlet_num, outlet_name)
        except (ValueError, IndexError) as e:
            _LOGGER.error("Error parsing outlet name: %s - %s", match, e)

    return outlet_names


def parse_outlet_state(response: str, outlet_num: int) -> Optional[bool]:
    """Parse the state of a specific outlet from command response."""
    if not response:
        _LOGGER.warning(
            "Empty response received when parsing outlet state for outlet %s",
            outlet_num,
        )
        return None

    # Log the entire response for debugging
    _LOGGER.debug("Full response for outlet %s:\n%s", outlet_num, response)

    # First check if this is a help or error message response
    if "Unknown command" in response or "Available commands" in response:
        _LOGGER.warning(
            "Command error when parsing outlet state for outlet %s. Response excerpt: %s",
            outlet_num,
            response[:200].replace("\n", " "),
        )
        return None

    # Look for outlet status from "power outlets status" command
    # Format: "Outlet N is (On|Off)"
    status_match = re.search(
        r"Outlet\s+%d\s+is\s+(On|Off)" % outlet_num,
        response,
        re.IGNORECASE | re.MULTILINE,
    )
    if status_match:
        state = status_match.group(1).lower() == "on"
        _LOGGER.debug(
            "Parsed outlet %s state (status pattern): %s",
            outlet_num,
            "ON" if state else "OFF",
        )
        return state

    # Try direct command pattern
    direct_match = re.search(
        r"outlet[s\s]+%d\s+(?:is\s+)?(on|off)" % outlet_num,
        response,
        re.IGNORECASE | re.MULTILINE,
    )
    if direct_match:
        state = direct_match.group(1).lower() == "on"
        _LOGGER.debug(
            "Parsed outlet %s state (direct pattern): %s",
            outlet_num,
            "ON" if state else "OFF",
        )
        return state

    # Try outletgroup pattern seen in logs
    outletgroup_match = re.search(
        r"outletgroup\s+%d\s+(?:is\s+)?(on|off)" % outlet_num,
        response,
        re.IGNORECASE | re.MULTILINE,
    )
    if outletgroup_match:
        state = outletgroup_match.group(1).lower() == "on"
        _LOGGER.debug(
            "Parsed outlet %s state (outletgroup pattern): %s",
            outlet_num,
            "ON" if state else "OFF",
        )
        return state

    # Try more generic patterns
    # Look for mention of the outlet number within 5 lines of "on" or "off"
    state_indicators = [
        (r"outlet\s+%d.*?(?:\n.*?){0,5}(on|active|enabled)" % outlet_num, True),
        (r"outlet\s+%d.*?(?:\n.*?){0,5}(off|inactive|disabled)" % outlet_num, False),
        (r"outletgroup\s+%d.*?(?:\n.*?){0,5}(on|active|enabled)" % outlet_num, True),
        (
            r"outletgroup\s+%d.*?(?:\n.*?){0,5}(off|inactive|disabled)" % outlet_num,
            False,
        ),
    ]

    for pattern, state_value in state_indicators:
        if re.search(pattern, response, re.IGNORECASE | re.MULTILINE):
            _LOGGER.debug(
                "Parsed outlet %s state (proximity pattern): %s",
                outlet_num,
                "ON" if state_value else "OFF",
            )
            return state_value

    # Try a simple keyword search approach as a last resort
    # This assumes that any mention of the specific outlet number followed by on/off indicates state
    response_lower = response.lower()
    outlet_str = f"outlet {outlet_num}"
    outletgroup_str = f"outletgroup {outlet_num}"

    # Check if the outlet is mentioned at all
    if (
        outlet_str in response_lower
        or outletgroup_str in response_lower
        or f"outlet{outlet_num}" in response_lower
    ):
        # If outlet is mentioned, check for on/off indicators
        indicators = [
            ("on", True),
            ("enabled", True),
            ("active", True),
            ("off", False),
            ("disabled", False),
            ("inactive", False),
        ]

        for keyword, state_value in indicators:
            if keyword in response_lower:
                _LOGGER.debug(
                    "Parsed outlet %s state (keyword pattern): %s",
                    outlet_num,
                    "ON" if state_value else "OFF",
                )
                return state_value

    # If we couldn't find the state in expected formats, log the issue
    _LOGGER.warning(
        "Could not find outlet state in response for outlet %s. Response excerpt: %s",
        outlet_num,
        response[:200].replace("\n", " "),
    )
    return None


def parse_power_factor(value_str: str) -> Optional[float]:
    """Parse power factor which might be '---' or a numeric value."""
    if not value_str or value_str.strip() == "---" or "no reading" in value_str.lower():
        return None

    # Extract numeric part
    match = re.search(r"([\d.]+)", value_str)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return None


def parse_outlet_details(response: str, outlet_num: int) -> Dict[str, Any]:
    """Parse outlet details from 'show outlets X details' response."""
    if not response:
        return {}

    outlet_data = {"outlet_number": outlet_num}

    # Parse power state (for switch entities)
    state = parse_outlet_state(response, outlet_num)
    if state is not None:
        outlet_data["state"] = "on" if state else "off"

    # Parse current (for current sensor)
    current_match = re.search(r"RMS Current:\s*([\d.]+)\s*A", response, re.IGNORECASE)
    if current_match:
        try:
            current = float(current_match.group(1))
            outlet_data["current"] = current
            _LOGGER.debug("Parsed outlet %s current: %s A", outlet_num, current)
        except ValueError:
            _LOGGER.error("Could not convert current value: %s", current_match.group(1))

    # Parse voltage (for voltage sensor)
    voltage_match = re.search(r"RMS Voltage:\s*([\d.]+)\s*V", response, re.IGNORECASE)
    if voltage_match:
        try:
            voltage = float(voltage_match.group(1))
            outlet_data["voltage"] = voltage
            _LOGGER.debug("Parsed outlet %s voltage: %s V", outlet_num, voltage)
        except ValueError:
            _LOGGER.error("Could not convert voltage value: %s", voltage_match.group(1))

    # Parse power (for power sensor)
    power_match = re.search(r"Active Power:\s*([\d.]+)\s*W", response, re.IGNORECASE)
    if power_match:
        try:
            power = float(power_match.group(1))
            outlet_data["power"] = power
            _LOGGER.debug("Parsed outlet %s power: %s W", outlet_num, power)
        except ValueError:
            _LOGGER.error("Could not convert power value: %s", power_match.group(1))

    # Parse apparent power (for power sensor)
    apparent_power_match = re.search(
        r"Apparent Power:\s*([\d.]+)\s*VA", response, re.IGNORECASE
    )
    if apparent_power_match:
        try:
            apparent_power = float(apparent_power_match.group(1))
            outlet_data["apparent_power"] = apparent_power
            _LOGGER.debug(
                "Parsed outlet %s apparent power: %s VA", outlet_num, apparent_power
            )
        except ValueError:
            _LOGGER.error(
                "Could not convert apparent power value: %s",
                apparent_power_match.group(1),
            )

    # Parse power factor - handle "---" or "(no reading)" cases
    pf_match = re.search(r"Power Factor:\s*([^(\r\n]+)", response, re.IGNORECASE)
    if pf_match:
        power_factor = parse_power_factor(pf_match.group(1))
        if power_factor is not None:
            outlet_data["power_factor"] = power_factor
            _LOGGER.debug("Parsed outlet %s power factor: %s", outlet_num, power_factor)
        else:
            outlet_data["power_factor"] = None
            _LOGGER.debug("Power factor has no reading for outlet %s", outlet_num)

    # Parse energy (for energy sensor)
    energy_match = re.search(r"Active Energy:\s*([\d.]+)\s*Wh", response, re.IGNORECASE)
    if energy_match:
        try:
            energy = float(energy_match.group(1))
            outlet_data["energy"] = energy
            _LOGGER.debug("Parsed outlet %s energy: %s Wh", outlet_num, energy)
        except ValueError:
            _LOGGER.error("Could not convert energy value: %s", energy_match.group(1))

    # Parse line frequency
    frequency_match = re.search(
        r"Line Frequency:\s*([\d.]+)\s*Hz", response, re.IGNORECASE
    )
    if frequency_match:
        try:
            frequency = float(frequency_match.group(1))
            outlet_data["line_frequency"] = frequency
            _LOGGER.debug("Parsed outlet %s frequency: %s Hz", outlet_num, frequency)
        except ValueError:
            _LOGGER.error(
                "Could not convert frequency value: %s", frequency_match.group(1)
            )

    # Parse non-critical flag
    non_critical_match = re.search(
        r"Non critical:\s*(True|False)", response, re.IGNORECASE
    )
    if non_critical_match:
        non_critical = non_critical_match.group(1).lower() == "true"
        outlet_data["non_critical"] = non_critical
        _LOGGER.debug("Parsed outlet %s non-critical: %s", outlet_num, non_critical)

    return outlet_data


def parse_pdu_power_data(response: str) -> Dict[str, Any]:
    """Parse PDU power data from 'show pdu power' or 'show inlets all details' response."""
    if not response:
        return {}

    result = {}

    # Log full response for debugging
    _LOGGER.debug("Parsing PDU power data from response of length %d", len(response))

    # Try different patterns for power data (from different command outputs)

    # Pattern 1: Standard PDU power format
    power_match = re.search(r"Power:\s*([\d.]+)\s*W", response)
    if power_match:
        try:
            result["power"] = float(power_match.group(1))
            _LOGGER.debug("Found power: %s W", result["power"])
        except (ValueError, TypeError):
            pass

    # Pattern 2: Alternative format "Active Power"
    if "power" not in result:
        alt_power_match = re.search(
            r"Active Power:\s*([\d.]+)\s*W", response, re.IGNORECASE
        )
        if alt_power_match:
            try:
                result["power"] = float(alt_power_match.group(1))
                _LOGGER.debug("Found active power: %s W", result["power"])
            except (ValueError, TypeError):
                pass

    # Parse current - try different formats
    current_match = re.search(r"Current:\s*([\d.]+)\s*A", response)
    if not current_match:
        current_match = re.search(
            r"RMS Current:\s*([\d.]+)\s*A", response, re.IGNORECASE
        )

    if current_match:
        try:
            result["current"] = float(current_match.group(1))
            _LOGGER.debug("Found current: %s A", result["current"])
        except (ValueError, TypeError):
            pass

    # Parse voltage - try different formats
    voltage_match = re.search(r"Voltage:\s*([\d.]+)\s*V", response)
    if not voltage_match:
        voltage_match = re.search(
            r"RMS Voltage:\s*([\d.]+)\s*V", response, re.IGNORECASE
        )

    if voltage_match:
        try:
            result["voltage"] = float(voltage_match.group(1))
            _LOGGER.debug("Found voltage: %s V", result["voltage"])
        except (ValueError, TypeError):
            pass

    # Parse energy - handle both kWh and Wh formats
    energy_match = re.search(r"Energy:\s*([\d.]+)\s*(?:kW|W)h", response)
    if not energy_match:
        energy_match = re.search(
            r"Active Energy:\s*([\d.]+)\s*(?:kW|W)h", response, re.IGNORECASE
        )

    if energy_match:
        try:
            # Convert kWh to Wh if needed
            energy_value = float(energy_match.group(1))
            if "kWh" in response:
                energy_value *= 1000
            result["energy"] = energy_value
            _LOGGER.debug("Found energy: %s Wh", result["energy"])
        except (ValueError, TypeError):
            pass

    # Parse power factor if available
    pf_match = re.search(r"Power Factor:\s*([^(\r\n]+)", response, re.IGNORECASE)
    if pf_match:
        power_factor = parse_power_factor(pf_match.group(1))
        if power_factor is not None:
            result["power_factor"] = power_factor
            _LOGGER.debug("Found power factor: %s", result["power_factor"])

    # Parse frequency if available
    freq_match = re.search(r"Line Frequency:\s*([\d.]+)\s*Hz", response, re.IGNORECASE)
    if freq_match:
        try:
            result["frequency"] = float(freq_match.group(1))
            _LOGGER.debug("Found frequency: %s Hz", result["frequency"])
        except (ValueError, TypeError):
            pass

    return result


def parse_pdu_temperature(response: str) -> Dict[str, Any]:
    """Parse PDU temperature from 'show pdu temperature' response."""
    if not response:
        return {}

    result = {}

    # Log full response for debugging
    _LOGGER.debug(
        "Parsing PDU temperature data from response of length %d", len(response)
    )

    # Try different temperature formats
    # Pattern 1: Standard format
    temp_match = re.search(r"Temperature:\s*([\d.]+)\s*[CF]", response)
    if not temp_match:
        # Pattern 2: Alternative format
        temp_match = re.search(
            r"Internal Temperature:\s*([\d.]+)\s*[CF]", response, re.IGNORECASE
        )

    if temp_match:
        try:
            result["temperature"] = float(temp_match.group(1))
            _LOGGER.debug("Found temperature: %s", result["temperature"])
        except (ValueError, TypeError):
            pass

    return result


def normalize_model_name(model_string: str) -> str:
    """Normalize model name from various formats to a standard format."""
    if not model_string:
        return "RLNK-P920R"  # Default to a supported model if none provided

    # Remove any whitespace and convert to uppercase
    model = model_string.strip().upper()

    # Check for common patterns in model numbers
    # If it's just "RLNK" or "RACKLINK", use default
    if model in ["RLNK", "RACKLINK"]:
        return "RLNK-P920R"  # Default to common Premium+ model

    # Handle Premium+ series variations
    if "RLNK-P" in model:
        # For Premium+ series PDUs
        # Support all variations from the lua file
        if model in [
            "RLNK-P415",
            "RLNK-P420",
            "RLNK-P915R",
            "RLNK-P915R-SP",
            "RLNK-P920R",
            "RLNK-P920R-SP",
        ]:
            return model
        # Try to match partial model numbers to supported models
        elif "P415" in model:
            return "RLNK-P415"
        elif "P420" in model:
            return "RLNK-P420"
        elif "P915R-SP" in model:
            return "RLNK-P915R-SP"
        elif "P915R" in model:
            return "RLNK-P915R"
        elif "P920R-SP" in model:
            return "RLNK-P920R-SP"
        elif "P920R" in model:
            return "RLNK-P920R"
        else:
            # Default to P920R as a safe fallback within Premium+ series
            return "RLNK-P920R"

    # Handle cases where RLNK prefix might be missing
    if model.startswith("P"):
        if "415" in model:
            return "RLNK-P415"
        elif "420" in model:
            return "RLNK-P420"
        elif "915R-SP" in model:
            return "RLNK-P915R-SP"
        elif "915R" in model:
            return "RLNK-P915R"
        elif "920R-SP" in model:
            return "RLNK-P920R-SP"
        elif "920R" in model:
            return "RLNK-P920R"

    # Default to P920R as the safest option
    return "RLNK-P920R"


def is_command_prompt(line: str) -> bool:
    """
    Check if a line is a command prompt.
    The format is typically: [DeviceName] #
    """
    return bool(re.search(r"^\[.+\]\s+#\s*$", line.strip()))


def extract_device_name_from_prompt(prompt: str) -> Optional[str]:
    """Extract device name from a command prompt."""
    match = re.search(r"^\[(.+)\]\s+#\s*$", prompt.strip())
    if match:
        return match.group(1)
    return None


def parse_available_commands(response: str) -> List[str]:
    """Parse available commands from a help response."""
    if not response or "available commands" not in response.lower():
        return []

    # Extract the commands section
    commands_section = response.lower().split("available commands:")[1].strip()

    # Extract command names
    command_lines = commands_section.split("\n")
    commands = []

    for line in command_lines:
        # Pattern: commandName  Description
        parts = line.strip().split(None, 1)
        if parts and parts[0]:
            commands.append(parts[0].strip())

    return commands


def parse_inlet_data(response: str) -> Dict[str, Any]:
    """Parse inlet data from 'show inlets all details' response."""
    if not response:
        return {}

    # This is essentially a wrapper for parse_pdu_power_data
    # with special inlet-specific handling if needed in the future
    return parse_pdu_power_data(response)
