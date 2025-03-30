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

    # More robust regex that handles formatting variations
    outlet_blocks = re.findall(
        r"Outlet (\d+)[^\n]*\n\s*Power state:\s*(\w+)",
        response,
        re.MULTILINE,
    )

    # If standard pattern fails, try alternative patterns
    if not outlet_blocks:
        _LOGGER.debug("Standard outlet pattern failed, trying alternative patterns")

        # Try alternative pattern with "Status" instead of "Power state"
        outlet_blocks = re.findall(
            r"Outlet (\d+)[^\n]*\n\s*Status:\s*(\w+)",
            response,
            re.MULTILINE,
        )

        # Try another pattern for different format
        if not outlet_blocks:
            outlet_blocks = re.findall(
                r"Outlet\s+(\d+).*?state[^:]*:\s*(\w+)",
                response,
                re.IGNORECASE | re.DOTALL,
            )

            # Last resort pattern - try to find ON/OFF within proximity
            if not outlet_blocks:
                # Look for any outlet number followed by ON or OFF within a few lines
                raw_blocks = re.findall(
                    r"Outlet\s+(\d+).*?(?:\n.*?){0,3}(ON|OFF|On|Off)",
                    response,
                    re.IGNORECASE | re.DOTALL,
                )
                if raw_blocks:
                    _LOGGER.debug(
                        "Found %d outlets using proximity pattern", len(raw_blocks)
                    )
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
            state = state_text == "on"

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
    """Parse the state of a specific outlet from 'show outlets X details' response."""
    if not response:
        _LOGGER.warning(
            "Empty response received when parsing outlet state for outlet %s",
            outlet_num,
        )
        return None

    # Log the entire response for debugging
    _LOGGER.debug("Full response for outlet %s:\n%s", outlet_num, response)

    # First try the standard format
    state_match = re.search(r"Power state:\s*(\w+)", response, re.IGNORECASE)
    if state_match:
        state = state_match.group(1).lower() == "on"
        _LOGGER.debug(
            "Parsed outlet %s state: %s", outlet_num, "ON" if state else "OFF"
        )
        return state

    # If standard format fails, try alternative formats
    alt_match = re.search(
        r"Outlet\s+%d.*?state\s*[:=]\s*(\w+)" % outlet_num,
        response,
        re.IGNORECASE | re.DOTALL,
    )
    if alt_match:
        state = alt_match.group(1).lower() == "on"
        _LOGGER.debug(
            "Parsed outlet %s state (alternative format): %s",
            outlet_num,
            "ON" if state else "OFF",
        )
        return state

    # Try another alternative format that might be used
    alt_match2 = re.search(
        r"Outlet\s+%d[^\n]*\n\s*Status:\s*(\w+)" % outlet_num,
        response,
        re.IGNORECASE | re.DOTALL,
    )
    if alt_match2:
        state = alt_match2.group(1).lower() == "on"
        _LOGGER.debug(
            "Parsed outlet %s state (Status field): %s",
            outlet_num,
            "ON" if state else "OFF",
        )
        return state

    # Try to find any on/off within a reasonable proximity of the outlet number
    proximity_match = re.search(
        r"(?:Outlet|Port)\s+%d.*?(?:\n.*?){0,5}(?:ON|OFF|On|Off)" % outlet_num,
        response,
        re.IGNORECASE | re.DOTALL,
    )
    if proximity_match:
        state_text = proximity_match.group(0).lower()
        _LOGGER.debug("Found potential state context: %s", state_text)
        # Check which state was found (last occurrence)
        if "off" in state_text.split()[-1].lower():
            _LOGGER.debug("Parsed outlet %s state (proximity match): OFF", outlet_num)
            return False
        elif "on" in state_text.split()[-1].lower():
            _LOGGER.debug("Parsed outlet %s state (proximity match): ON", outlet_num)
            return True

    # Dump response content for debugging failed cases
    # Show more of the response to help diagnose issues
    _LOGGER.warning(
        "Could not parse outlet state from response for outlet %s. Response excerpt: %s",
        outlet_num,
        response.replace("\n", " ")[
            :500
        ],  # Show more content but with newlines removed
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
    """Parse PDU power data from 'show pdu power' response."""
    if not response:
        return {}

    result = {}

    # Parse power
    power_match = re.search(r"Power:\s*([\d.]+)\s*W", response)
    if power_match:
        try:
            result["power"] = float(power_match.group(1))
        except (ValueError, TypeError):
            pass

    # Parse current
    current_match = re.search(r"Current:\s*([\d.]+)\s*A", response)
    if current_match:
        try:
            result["current"] = float(current_match.group(1))
        except (ValueError, TypeError):
            pass

    # Parse voltage
    voltage_match = re.search(r"Voltage:\s*([\d.]+)\s*V", response)
    if voltage_match:
        try:
            result["voltage"] = float(voltage_match.group(1))
        except (ValueError, TypeError):
            pass

    # Parse energy
    energy_match = re.search(r"Energy:\s*([\d.]+)\s*(?:kW|W)h", response)
    if energy_match:
        try:
            # Convert kWh to Wh if needed
            energy_value = float(energy_match.group(1))
            if "kWh" in response:
                energy_value *= 1000
            result["energy"] = energy_value
        except (ValueError, TypeError):
            pass

    return result


def parse_pdu_temperature(response: str) -> Dict[str, Any]:
    """Parse PDU temperature from 'show pdu temperature' response."""
    if not response:
        return {}

    result = {}

    # Parse temperature
    temp_match = re.search(r"Temperature:\s*([\d.]+)\s*[CF]", response)
    if temp_match:
        try:
            result["temperature"] = float(temp_match.group(1))
        except (ValueError, TypeError):
            pass

    return result


def normalize_model_name(model_string: str) -> str:
    """Normalize model name from various formats to a standard format."""
    if not model_string:
        return "DEFAULT"

    # Remove any whitespace and convert to uppercase
    model = model_string.strip().upper()

    # Check for common patterns in model numbers
    # If it's just "RLNK" or "RACKLINK", use default
    if model in ["RLNK", "RACKLINK"]:
        return "RLNK-P920R"  # Default to common model

    # Handle model variations
    if "RLNK-P" in model:
        # For Premium series PDUs
        return model
    elif "RLNK-" in model:
        # For standard series, normalize the format
        return model
    elif "RLNK" in model:
        # Some models might be reported as RLNKP920 instead of RLNK-P920
        # Insert hyphen if missing
        if "RLNKP" in model and "-" not in model:
            return model.replace("RLNKP", "RLNK-P")
        # Other normalization
        return model
    elif "P920" in model or "P915" in model:
        # Sometimes just the model number is reported
        return f"RLNK-{model}"

    # If no match, return the original with RLNK- prefix
    if not model.startswith("RLNK-"):
        return f"RLNK-{model}"

    return model


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
