# Middle Atlantic RackLink Integration for Home Assistant

![Home Assistant](https://img.shields.io/badge/Home%20Assistant-Integration-blue)
![License](https://img.shields.io/github/license/mckay115/homeassistant-middleatlantic-racklink)
![Version](https://img.shields.io/github/v/release/mckay115/homeassistant-middleatlantic-racklink?include_prereleases)

## Overview

This custom integration enables Home Assistant to interface with Middle Atlantic RackLink API-enabled devices such as Power Sequencers and UPS units. It allows you to monitor and control these devices directly from your Home Assistant setup, providing enhanced automation and management capabilities.

## Features

- Monitor the status of RackLink-enabled devices (voltage, current, power, etc.)
- Control power outlets (on/off/cycle)
- Name outlets and PDUs
- Integration with Home Assistant automation and scripts
- Support for multiple devices

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
   - Port (default: 23)
   - Username
   - Password
4. Click Submit to add the integration

## Usage

After adding the integration, your RackLink device will appear as entities in Home Assistant:

- **Binary sensors**: For status information (e.g., surge protection)
- **Sensors**: For measurements (voltage, current, power, temperature, etc.)
- **Switches**: For controlling outlets (on/off)

You can use these entities in automations, dashboards, and scripts.

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

### Deploying to a test Home Assistant instance

The repository includes a GitHub Actions workflow for automated deployment to a test Home Assistant instance. To use it:

1. Set up the following secrets in your GitHub repository:
   - `HA_SSH_PRIVATE_KEY`: SSH private key for connecting to your HA instance
   - `HA_SSH_HOST`: Hostname or IP address of your HA instance
   - `HA_SSH_USER`: SSH username
   - `HA_SSH_PORT`: SSH port (optional, defaults to 22)
   - `HA_CUSTOM_COMPONENTS_DIR`: Path to custom_components directory (optional, defaults to /config/custom_components)

2. Trigger the deployment workflow manually from the Actions tab or by pushing to the main branch.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
