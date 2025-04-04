"""Parser for Middle Atlantic Racklink responses."""

from typing import Any, Dict, List, Optional, Tuple, Callable
from dataclasses import dataclass

import logging
import re

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


# Helper function to process found matches
def _process_outlet_matches(matches: List[Tuple[str, str]]) -> Dict[int, bool]:
    outlet_states = {}
    for match in matches:
        try:
            outlet_num = int(match[0])
            state_text = match[1].lower()
            state = state_text in ("on", "active", "enabled", "true", "1")
            outlet_states[outlet_num] = state
            _LOGGER.debug(
                "Found outlet %d state: %s (from text: '%s')",
                outlet_num,
                "ON" if state else "OFF",
                match[1],
            )
        except (ValueError, IndexError) as e:
            _LOGGER.error("Error parsing outlet block: %s - %s", match, e)
    return outlet_states


# Define patterns to try in order
OUTLET_STATE_PATTERNS = [
    # Pattern 1: Standard format with "Power state:"
    (
        r"Outlet (\\d+)[^\\n]*\\n\\s*Power state:\\s*(\\w+)",
        re.MULTILINE,
        "Standard Power state",
    ),
    # Pattern 2: Using "Status:" instead of "Power state:"
    (
        r"Outlet (\\d+)[^\\n]*\\n\\s*Status:\\s*(\\w+)",
        re.MULTILINE,
        "Standard Status",
    ),
    # Pattern 3: More generic pattern looking for state/status
    (
        r"Outlet\\s+(\\d+).*?(?:state|status)[^:]*:\\s*(\\w+)",
        re.IGNORECASE | re.DOTALL,
        "Generic state/status",
    ),
    # Pattern 4: Looking for direct "Outlet X is ON/OFF" format
    (
        r"Outlet\\s+(\\d+)\\s+is\\s+(ON|OFF|On|Off)",
        re.IGNORECASE,
        "Direct is ON/OFF",
    ),
    # Pattern 5: Looking for "Outlet X ... ON/OFF" within proximity
    (
        r"Outlet\\s+(\\d+).*?(?:\\n.*?){0,5}(ON|OFF|On|Off)",
        re.IGNORECASE | re.DOTALL,
        "Proximity ON/OFF",
    ),
    # Pattern 6: Extreme fallback - just look for numbers followed by on/off
    (
        r"(\\d+)(?:[^a-zA-Z0-9]{1,20})(on|off)",
        re.DOTALL,
        "Fallback number ON/OFF",
    ),
]


def parse_all_outlet_states(response: str) -> Dict[int, bool]:
    """Parse all outlet states from 'show outlets all' response."""
    if not response:
        _LOGGER.warning("Empty response received for parsing all outlet states")
        return {}

    _LOGGER.debug(
        "Full response for parse_all_outlet_states (length %d)", len(response)
    )

    for pattern, flags, name in OUTLET_STATE_PATTERNS:
        _LOGGER.debug("Trying outlet state pattern: %s", name)
        outlet_blocks = re.findall(pattern, response, flags)

        # Special handling for fallback pattern where response is lowercased
        if name == "Fallback number ON/OFF" and not outlet_blocks:
            outlet_blocks = re.findall(pattern, response.lower(), flags)

        if outlet_blocks:
            _LOGGER.debug(
                "Found %d outlet blocks using pattern '%s'", len(outlet_blocks), name
            )
            outlet_states = _process_outlet_matches(outlet_blocks)
            if outlet_states:
                _LOGGER.debug(
                    "Successfully parsed %d outlet states", len(outlet_states)
                )
                return outlet_states
            _LOGGER.warning("Pattern '%s' found blocks but failed processing", name)
        else:
            _LOGGER.debug("Pattern '%s' did not find any outlet blocks", name)

    _LOGGER.warning(
        "No outlet blocks found in response - parser could not detect any outlets"
    )
    _LOGGER.debug("First 200 chars of response: %s", response[:200].replace("\n", " "))
    return {}


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


# Define patterns for single outlet state parsing
SINGLE_OUTLET_STATE_PATTERNS = [
    # Pattern 1: Specific for 'show outlet X'
    (r"Power state:\s*(\w+)", re.IGNORECASE, "Power state"),
    # Pattern 2: Using 'Status:'
    (r"Status:\s*(\w+)", re.IGNORECASE, "Status"),
    # Pattern 3: General state/status
    (r"(?:state|status)[^:]*:\s*(\w+)", re.IGNORECASE, "Generic state/status"),
    # Pattern 4: Direct ON/OFF state in response
    (r"\b(ON|OFF)\b", re.IGNORECASE, "Direct ON/OFF"),
]


def parse_outlet_state(response: str, outlet_num: int) -> Optional[bool]:
    """Parse the state of a specific outlet from command response."""
    if not response:
        _LOGGER.warning(
            "Empty response received when parsing outlet state for outlet %d",
            outlet_num,
        )
        return None

    # Log the entire response for debugging
    _LOGGER.debug(
        "Full response for outlet %d (len %d): %.500s...",
        outlet_num,
        len(response),
        response,
    )

    # Check for help/error messages first
    if "Unknown command" in response or "Available commands" in response:
        _LOGGER.warning(
            "Received help/error message when querying outlet %d: %s",
            outlet_num,
            response[:100],
        )
        return None

    # Iterate through patterns to find the state
    for pattern, flags, name in SINGLE_OUTLET_STATE_PATTERNS:
        match = re.search(pattern, response, flags)
        if match:
            state_text = match.group(1).lower()
            state = state_text in ("on", "active", "enabled", "true", "1")
            _LOGGER.debug(
                "Parsed outlet %d state using pattern '%s': %s (text: '%s')",
                outlet_num,
                name,
                "ON" if state else "OFF",
                match.group(1),
            )
            return state

    # If no pattern matched
    _LOGGER.warning(
        "Could not parse outlet state for outlet %d. Response: %.100s",
        outlet_num,
        response,
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


@dataclass
class MetricExtractionConfig:
    """Configuration for extracting a metric using regex."""

    metric_key: str
    patterns: List[Tuple[str, re.RegexFlag]]
    unit_expected: Optional[str] = None
    multiplier: float = 1.0
    parser_func: Callable = float


# Define Metric Configurations as Constants
OUTLET_CURRENT_CONFIG = MetricExtractionConfig(
    "current", [(r"RMS Current:\s*([\d.]+)\s*A", re.IGNORECASE)]
)
OUTLET_VOLTAGE_CONFIG = MetricExtractionConfig(
    "voltage", [(r"RMS Voltage:\s*([\d.]+)\s*V", re.IGNORECASE)]
)
OUTLET_POWER_CONFIG = MetricExtractionConfig(
    "power", [(r"Active Power:\s*([\d.]+)\s*W", re.IGNORECASE)]
)
OUTLET_APPARENT_POWER_CONFIG = MetricExtractionConfig(
    "apparent_power", [(r"Apparent Power:\s*([\d.]+)\s*VA", re.IGNORECASE)]
)
OUTLET_PF_STR_CONFIG = MetricExtractionConfig(
    "power_factor_str",
    [(r"Power Factor:\s*([^(\r\n]+)", re.IGNORECASE)],
    parser_func=str,
)
OUTLET_ENERGY_CONFIG = MetricExtractionConfig(
    "energy", [(r"Active Energy:\s*([\d.]+)\s*Wh", re.IGNORECASE)]
)
OUTLET_FREQUENCY_CONFIG = MetricExtractionConfig(
    "line_frequency", [(r"Line Frequency:\s*([\d.]+)\s*Hz", re.IGNORECASE)]
)
OUTLET_NON_CRITICAL_STR_CONFIG = MetricExtractionConfig(
    "non_critical_str",
    [(r"Non critical:\s*(True|False)", re.IGNORECASE)],
    parser_func=str,
)

PDU_POWER_CONFIG = MetricExtractionConfig(
    "power",
    [
        (r"Power:\s*([\d.]+)\s*W", re.RegexFlag(0)),
        (r"Active Power:\s*([\d.]+)\s*W", re.IGNORECASE),
    ],
)
PDU_CURRENT_CONFIG = MetricExtractionConfig(
    "current",
    [
        (r"Current:\s*([\d.]+)\s*A", re.RegexFlag(0)),
        (r"RMS Current:\s*([\d.]+)\s*A", re.IGNORECASE),
    ],
)
PDU_VOLTAGE_CONFIG = MetricExtractionConfig(
    "voltage",
    [
        (r"Voltage:\s*([\d.]+)\s*V", re.RegexFlag(0)),
        (r"RMS Voltage:\s*([\d.]+)\s*V", re.IGNORECASE),
    ],
)
PDU_ENERGY_PATTERNS = [
    (r"Energy:\s*([\d.]+)\s*(?:kW|W)h", re.RegexFlag(0)),
    (r"Active Energy:\s*([\d.]+)\s*(?:kW|W)h", re.IGNORECASE),
]
PDU_PF_STR_CONFIG = MetricExtractionConfig(
    "power_factor_str",
    [(r"Power Factor:\s*([^(\r\n]+)", re.IGNORECASE)],
    parser_func=str,
)
PDU_FREQUENCY_CONFIG = MetricExtractionConfig(
    "frequency", [(r"Line Frequency:\s*([\d.]+)\s*Hz", re.IGNORECASE)]
)


# Helper to extract a metric using multiple regex patterns
def _extract_metric(response: str, config: MetricExtractionConfig) -> Optional[Any]:
    """Extracts a metric value from response using a list of regex patterns."""
    for pattern, flags in config.patterns:
        match = re.search(pattern, response, flags)
        if match:
            try:
                value = config.parser_func(match.group(1))
                # Apply multiplier logic (needs refinement based on actual use)
                if config.unit_expected and config.multiplier != 1.0:
                    # Simple check if unit is in the matched string part
                    # This might need to be more robust
                    if config.unit_expected in match.group(0):
                        value *= config.multiplier

                _LOGGER.debug(
                    "Found %s: %s (using pattern: %s) ",
                    config.metric_key,
                    value,
                    pattern,
                )
                return value
            except (ValueError, TypeError, IndexError):
                _LOGGER.warning(
                    "Could not parse %s value from '%s' using pattern %s",
                    config.metric_key,
                    match.group(1),
                    pattern,
                )
                # Continue to next pattern if parsing fails

    _LOGGER.debug("Metric '%s' not found using provided patterns.", config.metric_key)
    return None


def parse_outlet_details(response: str, outlet_num: int) -> Dict[str, Any]:
    """Parse outlet details from 'show outlets X details' response."""
    if not response:
        return {}

    outlet_data = {"outlet_number": outlet_num}
    _LOGGER.debug(
        "Parsing outlet %d details from response of length %d",
        outlet_num,
        len(response),
    )

    # Parse state first (crucial for switch)
    state = parse_outlet_state(response, outlet_num)
    if state is not None:
        outlet_data["state"] = "on" if state else "off"

    # Use predefined config constants, creating new instances to update keys
    current = _extract_metric(
        response,
        MetricExtractionConfig(
            metric_key=f"outlet {outlet_num} current",
            patterns=OUTLET_CURRENT_CONFIG.patterns,
        ),
    )
    if current is not None:
        outlet_data["current"] = current

    voltage = _extract_metric(
        response,
        MetricExtractionConfig(
            metric_key=f"outlet {outlet_num} voltage",
            patterns=OUTLET_VOLTAGE_CONFIG.patterns,
        ),
    )
    if voltage is not None:
        outlet_data["voltage"] = voltage

    power = _extract_metric(
        response,
        MetricExtractionConfig(
            metric_key=f"outlet {outlet_num} power",
            patterns=OUTLET_POWER_CONFIG.patterns,
        ),
    )
    if power is not None:
        outlet_data["power"] = power

    apparent_power = _extract_metric(
        response,
        MetricExtractionConfig(
            metric_key=f"outlet {outlet_num} apparent power",
            patterns=OUTLET_APPARENT_POWER_CONFIG.patterns,
        ),
    )
    if apparent_power is not None:
        outlet_data["apparent_power"] = apparent_power

    # Parse power factor string then convert using parse_power_factor
    power_factor_str = _extract_metric(
        response,
        MetricExtractionConfig(
            metric_key=f"outlet {outlet_num} power factor str",
            patterns=OUTLET_PF_STR_CONFIG.patterns,
            parser_func=str,
        ),
    )
    if power_factor_str is not None:
        power_factor = parse_power_factor(power_factor_str)
        # Store None explicitly if parsing fails (e.g., "---")
        outlet_data["power_factor"] = power_factor
        _LOGGER.debug("Parsed outlet %d power factor: %s", outlet_num, power_factor)
    else:
        # Ensure key exists even if not found in response
        outlet_data["power_factor"] = None

    energy = _extract_metric(
        response,
        MetricExtractionConfig(
            metric_key=f"outlet {outlet_num} energy",
            patterns=OUTLET_ENERGY_CONFIG.patterns,
        ),
    )
    if energy is not None:
        outlet_data["energy"] = energy

    frequency = _extract_metric(
        response,
        MetricExtractionConfig(
            metric_key=f"outlet {outlet_num} frequency",
            patterns=OUTLET_FREQUENCY_CONFIG.patterns,
        ),
    )
    if frequency is not None:
        outlet_data["line_frequency"] = frequency

    # Parse boolean non-critical flag
    non_critical_str = _extract_metric(
        response,
        MetricExtractionConfig(
            metric_key=f"outlet {outlet_num} non-critical str",
            patterns=OUTLET_NON_CRITICAL_STR_CONFIG.patterns,
            parser_func=str,
        ),
    )
    if non_critical_str is not None:
        outlet_data["non_critical"] = non_critical_str.lower() == "true"
        _LOGGER.debug(
            "Parsed outlet %d non-critical: %s",
            outlet_num,
            outlet_data["non_critical"],
        )

    _LOGGER.debug("Final parsed data for outlet %d: %s", outlet_num, outlet_data)
    return outlet_data


def parse_pdu_power_data(response: str) -> Dict[str, Any]:
    """Parse PDU power data from 'show pdu power' or 'show inlets all details' response."""
    if not response:
        return {}

    result = {}
    _LOGGER.debug("Parsing PDU power data from response of length %d", len(response))

    # Extract metrics using helper and predefined configs
    power = _extract_metric(response, PDU_POWER_CONFIG)
    if power is not None:
        result["power"] = power

    current = _extract_metric(response, PDU_CURRENT_CONFIG)
    if current is not None:
        result["current"] = current

    voltage = _extract_metric(response, PDU_VOLTAGE_CONFIG)
    if voltage is not None:
        result["voltage"] = voltage

    # Special handling for energy conversion (kWh -> Wh)
    energy_value = None
    for pattern, flags in PDU_ENERGY_PATTERNS:
        match = re.search(pattern, response, flags)
        if match:
            try:
                energy_value = float(match.group(1))
                if "kWh" in match.group(0).lower():  # Check if matched unit was kWh
                    energy_value *= 1000
                _LOGGER.debug(
                    "Found energy: %s Wh (using pattern: %s)", energy_value, pattern
                )
                break  # Stop after first successful match
            except (ValueError, TypeError, IndexError):
                pass  # Try next pattern
    if energy_value is not None:
        result["energy"] = energy_value

    power_factor_str = _extract_metric(response, PDU_PF_STR_CONFIG)
    if power_factor_str is not None:
        power_factor = parse_power_factor(power_factor_str)
        if power_factor is not None:
            result["power_factor"] = power_factor
            _LOGGER.debug("Found power factor: %s", power_factor)

    frequency = _extract_metric(response, PDU_FREQUENCY_CONFIG)
    if frequency is not None:
        result["frequency"] = frequency

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

    model = model_string.strip().upper()

    # Known models mapping (more specific first)
    known_models = {
        "RLNK-P915R-SP": "RLNK-P915R-SP",
        "P915R-SP": "RLNK-P915R-SP",
        "RLNK-P920R-SP": "RLNK-P920R-SP",
        "P920R-SP": "RLNK-P920R-SP",
        "RLNK-P415": "RLNK-P415",
        "P415": "RLNK-P415",
        "RLNK-P420": "RLNK-P420",
        "P420": "RLNK-P420",
        "RLNK-P915R": "RLNK-P915R",
        "P915R": "RLNK-P915R",
        "RLNK-P920R": "RLNK-P920R",
        "P920R": "RLNK-P920R",
    }

    # Check exact matches
    if model in known_models:
        return known_models[model]

    # Partial matches (check if a known model key is *in* the input model string)
    # This handles cases like "Model: RLNK-P920R Foo Bar"
    # Check longer keys first to avoid premature matching (e.g., P915R before P915R-SP)
    for known_key in sorted(known_models.keys(), key=len, reverse=True):
        if known_key in model:
            return known_models[known_key]

    # Handle generic RLNK prefix
    if model.startswith("RLNK-P") or model == "RLNK" or model == "RACKLINK":
        return "RLNK-P920R"  # Default to common Premium+ model

    # If nothing matches, default to P920R as the safest option
    _LOGGER.warning(
        "Could not normalize model string '%s', defaulting to RLNK-P920R", model_string
    )
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


def _parse_value(value_str: str, unit: Optional[str] = None) -> Any:
    """Attempt to parse a string value into a float, int, or bool."""
    if not value_str:
        return None

    value_str = value_str.strip()

    # Remove unit suffixes if present
    if unit:
        value_str = value_str.replace(unit, "").strip()

    try:
        # Try parsing as float first
        return float(value_str)
    except ValueError:
        pass

    try:
        # Try parsing as int
        return int(value_str)
    except ValueError:
        pass

    # Handle boolean strings
    lower_val = value_str.lower()
    boolean_true_strings = {"true", "on", "yes", "active", "enabled"}
    boolean_false_strings = {"false", "off", "no", "inactive", "disabled"}
    if lower_val in boolean_true_strings:
        return True
    if lower_val in boolean_false_strings:
        return False

    # Handle specific known string values that map to None
    none_strings = {"none", "unknown", "not available"}
    if lower_val in none_strings:
        return None

    # Handle specific error string
    if lower_val == "error":
        _LOGGER.warning("Received 'error' value: %s", value_str)
        return None  # Treat 'error' as an unknown state

    # Return the original string if no parsing worked
    _LOGGER.debug("Could not parse value '%s', returning as string.", value_str)
    return value_str


def parse_system_power(response: str) -> Dict[str, Any]:
    """Parse system power information from 'show power' response."""
    if not response:
        return {}

    result = {}

    # Log full response for debugging
    _LOGGER.debug("Parsing system power data from response of length %d", len(response))

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
