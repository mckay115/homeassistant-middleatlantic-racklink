# Middle Atlantic RackLink Home Assistant Integration

This integration allows Home Assistant to control and monitor Middle Atlantic RackLink PDUs (Power Distribution Units).

**Important Note:** This integration works exclusively with the Premium+ Series PDUs. Standard Premium Series or other model lines are not supported.

## Features

- 🔌 Control outlets (turn on, turn off, cycle power)
- 📊 Monitor power data (current, voltage, power, energy)
- 🌡️ Temperature monitoring
- 📝 Customizable outlet names
- 🔍 Automatic device detection
- 🔒 Modern socket-based connection

## Supported Devices

This integration supports the following Middle Atlantic RackLink Premium+ PDU models:

- RLNK-P415
- RLNK-P420
- RLNK-P915R
- RLNK-P915R-SP
- RLNK-P920R
- RLNK-P920R-SP

Model number guide:
- The "P" prefix indicates Premium+ models (advanced features and controls)
- Numbers "15" and "20" indicate amperage rating (15A or 20A service)
- "R" suffix indicates rackmount/sequencer models
- "SP" indicates models with surge protection
- Models with part numbers ending in "20" (such as RLNK-P420) provide additional power monitoring features

## Installation

### HACS (Recommended)

1. Open HACS in your Home Assistant instance
2. Go to "Integrations"
3. Click the three dots in the top right corner and select "Custom repositories"
4. Add this repository URL: `https://github.com/yodazach/homeassistant-middleatlantic-racklink`
5. Select "Integration" as the category
6. Click "ADD"
7. Search for "Middle Atlantic RackLink" and install it
8. Restart Home Assistant

### Manual Installation

1. Download the latest release and copy the `custom_components/middle_atlantic_racklink` directory to your Home Assistant's `custom_components` directory
2. Restart Home Assistant

## Configuration

1. Go to Settings → Devices & Services
2. Click "Add Integration"
3. Search for "Middle Atlantic RackLink" and select it
4. Enter your PDU's IP address and port (default: 23)
5. If your PDU requires authentication, enter the username and password
6. Click "Submit" to add the integration

## Services

This integration provides the following services:

- `middle_atlantic_racklink.set_outlet_name`: Set a name for an outlet
- `middle_atlantic_racklink.set_pdu_name`: Set a name for the PDU
- `middle_atlantic_racklink.cycle_outlet`: Cycle power for a specific outlet
- `middle_atlantic_racklink.cycle_all_outlets`: Cycle power for all outlets

## Entities

### Switches

Each outlet on the PDU is represented as a switch entity, allowing you to turn it on or off.

### Sensors

Depending on your PDU model, the following sensors may be available:

- Power (W)
- Current (A)
- Voltage (V)
- Energy (kWh)
- Power Factor (%)
- Temperature (°C)
- Frequency (Hz)

Advanced models may also provide per-outlet power and current monitoring.

## Technical Details

This integration uses a direct socket connection to communicate with the PDU using asyncio, which provides better performance and compatibility with Home Assistant's asynchronous architecture. The connection is non-blocking and efficiently manages resources.

## Troubleshooting

If you encounter issues with the integration:

1. Check the Home Assistant logs for error messages related to "middle_atlantic_racklink"
2. Verify that your PDU is accessible from your Home Assistant instance
3. Test the connection to your PDU using: `nc -v <pdu_ip> <port>` or `telnet <pdu_ip> <port>`
4. If your PDU requires authentication, ensure the credentials are correct
5. Make sure no other devices or services are trying to connect to the PDU at the same time

### Enabling Debug Logging

To help troubleshoot issues, you can enable debug logging for this integration. Add the following to your `configuration.yaml` file:

```yaml
logger:
  default: info
  logs:
    custom_components.middle_atlantic_racklink: debug
```

This will show detailed logs including raw commands sent to the PDU and the responses received. After adding this, restart Home Assistant and check the logs for more detailed information.

The debug logs will show:
- Raw commands sent to the PDU
- Raw responses received from the PDU
- Parsed data and state changes
- Socket connection details
- Error messages with full context

After troubleshooting, it's recommended to remove the debug logging or set it back to `info` level to reduce log file size.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request or open an Issue on GitHub.

## License

This integration is licensed under the MIT License. See the LICENSE file for details.

## Credits

This integration is based on the work of multiple contributors and was created to improve compatibility with various Middle Atlantic RackLink PDU models.
