# Middle Atlantic RackLink Integration for Home Assistant

![Home Assistant](https://img.shields.io/badge/Home%20Assistant-Integration-blue)
![License](https://img.shields.io/github/license/mckay115/homeassistant-middleatlantic-racklink)
![Version](https://img.shields.io/github/v/release/mckay115/homeassistant-middleatlantic-racklink?include_prereleases)
![HACS](https://img.shields.io/badge/HACS-Custom-orange)
![Code Quality](https://img.shields.io/github/actions/workflow/status/mckay115/homeassistant-middleatlantic-racklink/code-quality.yaml?label=code%20quality)

## Overview

This custom integration enables Home Assistant to interface with Middle Atlantic RackLink API-enabled devices such as Power Sequencers and UPS units. It allows you to monitor and control these devices directly from your Home Assistant setup, providing enhanced automation and management capabilities.

## Features

- **Power Control**: Control individual outlets or all outlets (on/off/cycle)
- **Monitoring**: Track voltage, current, power consumption, energy use, temperature
- **Auto-discovery**: Integration automatically detects device model and configuration
- **Customization**: Name outlets and PDUs for easier identification
- **Robust Connection**: Automatic reconnection and error handling
- **Low Overhead**: Efficient background updates to reduce network traffic
- **Full Type Hints**: Complete type hinting for improved code quality
- **Comprehensive Logging**: Detailed logs for troubleshooting
- **Multiple Device Support**: Add multiple RackLink devices to a single HA instance

## Installation

### HACS (Recommended)

1. Ensure you have [HACS](https://hacs.xyz/) installed in your Home Assistant setup.
2. Go to HACS in the sidebar → Integrations → ⋮ (top right) → Custom repositories
3. Add `https://github.com/mckay115/homeassistant-middleatlantic-racklink` as an Integration
4. Click "Middle Atlantic RackLink" in the list of integrations and install it
5. Restart Home Assistant

### Manual Installation

#### Option 1: Using the release ZIP file

1. Download the latest release ZIP file from the [Releases page](https://github.com/mckay115/homeassistant-middleatlantic-racklink/releases)
2. Unzip and copy the `middle_atlantic_racklink` directory to your Home Assistant's `custom_components` directory
3. Restart Home Assistant

#### Option 2: Direct download

1. Create a `custom_components/middle_atlantic_racklink` directory in your Home Assistant configuration directory
2. Download the files from this repository and place them in the directory you created
3. Restart Home Assistant

## Configuration

1. Go to Settings → Devices & Services → Add Integration
2. Search for "Middle Atlantic RackLink" and select it
3. Enter the required information:
   - IP address or hostname of your RackLink device
   - Port (default: 6000)
   - Username (usually 'admin')
   - Password
4. Click Submit to add the integration

The integration will then test the connection, discover the device, and set up all available entities automatically.

## Entities Created

After adding the integration, your RackLink device will appear with the following entities:

### Sensors
- **Voltage**: Main input voltage in V
- **Current**: Main input current in A
- **Power**: Active power consumption in W
- **Energy**: Cumulative energy consumption in kWh
- **Temperature**: Internal temperature in °C
- **Frequency**: Power frequency in Hz
- **Power Factor**: Power factor percentage
- **Per-Outlet Metrics**: Each outlet gets its own power, current, energy, and power factor sensors
- **Apparent Power**: Apparent power of each outlet in VA
- **Voltage**: Individual outlet voltage in V
- **Line Frequency**: Frequency measurement at each outlet in Hz

### Switches
- **Outlets 1-8**: Individual outlet control (on/off)
- **All Outlets On**: Turn all outlets on
- **All Outlets Off**: Turn all outlets off

### Binary Sensors
- **Surge Protection**: Status of the surge protection feature
- **Outlet Non-Critical**: Non-critical status flag for each outlet (ON = non-critical, can be used for load shedding)

## Available Services

The integration provides several services for advanced control:

- **cycle_all_outlets**: Power cycle all outlets
- **cycle_outlet**: Power cycle a specific outlet
- **set_outlet_name**: Change the name of a specific outlet
- **set_pdu_name**: Change the name of the PDU device

## Dashboard Examples

Here's an example Lovelace card configuration for your RackLink PDU:

```yaml
type: entities
title: Server Rack Power
entities:
  - entity: sensor.racklink_voltage
  - entity: sensor.racklink_current
  - entity: sensor.racklink_power
  - entity: sensor.racklink_temperature
  - entity: binary_sensor.racklink_surge_protection
  - type: divider
  - entity: switch.outlet_1
  - entity: switch.outlet_2
  - entity: switch.outlet_3
  - entity: switch.outlet_4
  - type: divider
  - entity: switch.all_outlets_on
  - entity: switch.all_outlets_off
```

## Automation Examples

### Power Cycle a Device if Network Ping Fails

```yaml
alias: Auto Reboot Network Switch
description: "Power cycle the network switch if ping fails"
trigger:
  - platform: template
    value_template: "{{ states('binary_sensor.network_switch_ping') == 'off' }}"
condition:
  - condition: numeric_state
    entity_id: sensor.network_switch_ping_failures
    above: 3
action:
  - service: middle_atlantic_racklink.cycle_outlet
    data:
      outlet: 2
  - delay: 
      seconds: 60
  - service: notify.mobile_app
    data:
      title: "Network Switch Rebooted"
      message: "Power cycled the network switch due to ping failure"
```

## Development & Testing

### Setting up a development environment

```bash
# Clone the repository
git clone https://github.com/mckay115/homeassistant-middleatlantic-racklink
cd homeassistant-middleatlantic-racklink

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements-test.txt
pip install homeassistant

# Run tests
pytest tests/
```

### RackLink CLI Protocol Reference

The integration communicates with RackLink devices using their command-line interface (CLI) over a TCP socket connection (default port 6000). Below is a reference of the available commands and data that can be retrieved:

#### Authentication

The device expects telnet-style authentication:
```
Username: admin
Password: [your-password]
```

#### Power Control Commands

- `power outlets <outlet_num> on /y` - Turn specific outlet on
- `power outlets <outlet_num> off /y` - Turn specific outlet off
- `power outlets <outlet_num> cycle /y` - Power cycle specific outlet (off then on)

#### Information Commands

- `show outlets <outlet_num> details` - Get detailed information for a specific outlet
- `show outlets all` - List basic status for all outlets
- `show outlets all details` - Get detailed status for all outlets

#### Available Data Per Outlet

Each outlet provides the following data points:
- **Power state**: On/Off status
- **RMS Current**: Amperage in A (e.g., 0.114 A)
- **RMS Voltage**: Voltage in V (e.g., 120 V)
- **Line Frequency**: Frequency in Hz (e.g., 60.0 Hz)
- **Active Power**: Power consumption in W (e.g., 7 W)
- **Apparent Power**: VA rating (e.g., 14 VA)
- **Power Factor**: Efficiency ratio (e.g., 0.53)
- **Active Energy**: Total energy consumption in Wh (e.g., 632210 Wh)
- **Cycling power off period**: Duration in seconds (e.g., 5 s)
- **Non-critical flag**: True/False
- **Receptacle type**: Outlet type (e.g., NEMA 5-20R)
- **Rated current**: Maximum current rating in A (e.g., 16 A)
- **Operating voltage**: Voltage range (e.g., 100-150 V)

#### Testing Connection

A simple Python script (`test_racklink_socket.py`) is included in the repository to test communication with a RackLink device:

```bash
# Modify the script with your device's IP address, username, and password
python test_racklink_socket.py
```

The script performs basic operations (checking status, turning outlets on/off, cycling power) and collects detailed information about the device's capabilities.

### Deploying to a test Home Assistant instance

The repository includes a GitHub Actions workflow for automated deployment to a test Home Assistant instance. To use it:

1. Set up the following secrets in your GitHub repository:
   - `HA_SSH_PRIVATE_KEY`: SSH private key for connecting to your HA instance
   - `HA_SSH_HOST`: Hostname or IP address of your HA instance
   - `HA_SSH_USER`: SSH username
   - `HA_SSH_PORT`: SSH port (optional, defaults to 22)
   - `HA_CUSTOM_COMPONENTS_DIR`: Path to custom_components directory (optional, defaults to /config/custom_components)

2. Trigger the deployment workflow manually from the Actions tab or by pushing to the main branch.

## Troubleshooting

If you encounter issues with the integration:

1. **Check Connectivity**: Ensure your Home Assistant instance can reach the RackLink device on the network
2. **Verify Credentials**: Make sure the username and password are correct
3. **Check Logs**: Increase logging level for the integration by adding to configuration.yaml:
   ```yaml
   logger:
     default: warning
     logs:
       custom_components.middle_atlantic_racklink: debug
   ```
4. **Check Firmware**: Make sure your RackLink device is running supported firmware
5. **Report Issues**: If problems persist, [create an issue](https://github.com/mckay115/homeassistant-middleatlantic-racklink/issues) with your logs and device information

## Technical Implementation Notes

### Device Communication

This integration connects to Middle Atlantic RackLink devices using a TCP socket connection (default port 6000) and communicates with the device's telnet-based CLI interface. The device uses the following command structure:

```
# Power control
power outlets <outlet_num> on /y
power outlets <outlet_num> off /y
power outlets <outlet_num> cycle /y

# Information retrieval
show outlets <outlet_num> details
show outlets all
show outlets all details
```

### Data Availability

The RackLink devices provide a wealth of data for each outlet, including:

- Power state (On/Off)
- RMS Current (Amps)
- RMS Voltage (Volts)
- Line Frequency (Hz)
- Active Power (Watts)
- Apparent Power (VA)
- Power Factor
- Active Energy (Wh)
- Cycling power off period (seconds)
- Non-critical flag (True/False)
- Receptacle type (e.g., NEMA 5-20R)
- Rated current (Amps)
- Operating voltage range (e.g., 100-150V)

### Future Improvements

Potential improvements to the integration include:

- Exposing receptacle type, rated current, and operating voltage as device attributes
- Adding cycling period as a configurable value
- Implementing load shedding capabilities using the non-critical flags
- Supporting more device commands discovered through the `help` command
- Improving connection stability with better error recovery
- Optimizing data updates to reduce telnet traffic and improve responsiveness

The `test_racklink_socket.py` script is included in the repository for testing connection and data collection directly from RackLink devices.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Code Structure

The integration follows a modular design approach to improve maintainability and follows Home Assistant development standards:

- **api.py**: Contains the `RacklinkApi` class that handles the low-level socket communication with the RackLink device. This includes creating and managing socket connections, sending commands, and parsing raw responses.

- **device.py**: Contains the `RacklinkDevice` class that represents the PDU device and implements device-specific logic, using the API. This class manages device capabilities, state tracking, and high-level device operations.

- **coordinator.py**: Contains the `RacklinkDataUpdateCoordinator` class that handles data coordination between Home Assistant and the device, implementing the Home Assistant `DataUpdateCoordinator` pattern for efficient updates.

- **__init__.py**: Main integration entry point that sets up the device, coordinator, and platform entities.

- **const.py**: Contains constants used throughout the integration.

- **Platform files**: 
  - `switch.py`: Implements the switch platform for controlling outlets
  - `sensor.py`: Implements sensors for device metrics
  - `binary_sensor.py`: Implements binary sensors for device status
  - `button.py`: Implements buttons for device actions

This modular approach makes the code easier to maintain, test, and extend. Each module has a clear responsibility, making it easier to troubleshoot issues and add new features.

### Module Interaction

The modules interact in the following manner:

1. **Home Assistant ↔ Coordinator**: Home Assistant interacts primarily with the DataUpdateCoordinator to efficiently manage data updates and entity state refreshes.

2. **Coordinator ↔ Device**: The coordinator requests updates from the device, which maintains the state of the physical device.

3. **Device ↔ API**: The device uses the API to send commands and receive data from the physical device.

4. **Platform Entities ↔ Coordinator & Device**: Platform entities (switches, sensors, etc.) use both the coordinator for data updates and the device for executing commands.

```
Home Assistant ←→ Coordinator ←→ Device ←→ API ←→ Physical PDU
                      ↑             ↑
                      |             |
                      ↓             ↓
                   Platform Entities
```

This separation of concerns ensures that changes to one component (like improving the socket communication) don't require changes to other components (like the entity representation).
