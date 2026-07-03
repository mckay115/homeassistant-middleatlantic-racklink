# Middle Atlantic RackLink Home Assistant Integration

Control and monitor Middle Atlantic (Legrand) RackLink PDUs from Home Assistant.

**Note:** This integration supports Premium and Premium+ Series PDUs with control protocol capabilities.

## Features

- Outlet control: turn outlets on/off and cycle power (per outlet or all at once)
- Power monitoring: voltage, current, power, apparent power, power factor, frequency, and energy (with Home Assistant Energy dashboard support)
- Per-outlet power/energy/current/voltage sensors (Redfish mode)
- Load shedding and outlet sequencing controls (vendor features via telnet)
- Surge protection status
- Rename outlets and the PDU from Home Assistant
- Automatic discovery via mDNS/zeroconf
- Reauthentication and reconfiguration flows — change credentials, host, or protocol without removing the integration
- Diagnostics download for easier issue reporting

## Connection modes

| Mode | Transport | Default port | Notes |
|------|-----------|--------------|-------|
| **Redfish** (recommended) | HTTPS/HTTP REST API | 443 (HTTPS), 80 (HTTP fallback) | Fast 10-second polling, per-outlet metrics |
| **Telnet** | TCP text/binary protocol | 6000 (Premium), 60000 (Premium+ binary) | 60-second polling to protect the device session |
| **Auto** | Tries Redfish, then telnet ports | — | Detects the best available transport |

When Redfish is used and *vendor features* are enabled, the integration also opens a secondary telnet connection ("hybrid mode") for features Redfish does not expose: load shedding and outlet sequencing.

## Supported devices

RackLink PDU models with control protocol capabilities, including:

- **Premium Series** (telnet, port 6000): RLNK-P920R (verified) and other RLNK-P### models
- **Premium+ Series** (binary, port 60000, or Redfish): RLNK-P415, RLNK-P420, RLNK-P915R(-SP), RLNK-P920R(-SP)

## Installation

### HACS (recommended)

1. Open HACS in your Home Assistant instance
2. Add this repository as a custom repository (category: Integration): `https://github.com/mckay115/homeassistant-middleatlantic-racklink`
3. Install "Middle Atlantic RackLink"
4. Restart Home Assistant

### Manual

1. Copy `custom_components/middle_atlantic_racklink` into your Home Assistant `custom_components` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for "Middle Atlantic RackLink"
3. The integration scans for devices via mDNS; pick a discovered device or choose "Enter manually"
4. Enter the device credentials (default username is usually `admin`; the password is device-specific)
5. Pick the connection type (Redfish, Telnet, or Auto)

Devices advertised over zeroconf also appear automatically under **Settings → Devices & Services → Discovered**.

### Options

The polling interval can be tuned under the integration's **Configure** menu (5–300 seconds). Defaults are 10 s for Redfish and 60 s for telnet.

### Reconfigure and reauthentication

- If the device password changes, Home Assistant prompts for reauthentication automatically.
- Use the **Reconfigure** menu item on the integration entry to change the host, credentials, or connection type without deleting the entry.

## Entities

- **Switches**: one per outlet, with outlet metadata (power-on delays, rated current) as attributes
- **Sensors** (PDU): voltage, current, power, apparent power, power factor, frequency, energy
- **Sensors** (per outlet, Redfish): power, energy, current, voltage
- **Binary sensors**: surge protection, load shedding active, sequence active, per-outlet non-critical flag
- **Buttons**: cycle per outlet, cycle all outlets, start/stop load shedding, start/stop sequence (vendor-feature buttons appear only when a telnet channel is available)

## Services

All services target outlet switch entities of this integration:

| Service | Description |
|---------|-------------|
| `middle_atlantic_racklink.cycle_outlet` | Cycle power on the targeted outlet(s) |
| `middle_atlantic_racklink.cycle_all_outlets` | Cycle power on every outlet of the PDU |
| `middle_atlantic_racklink.set_outlet_name` | Rename the targeted outlet on the device |
| `middle_atlantic_racklink.set_pdu_name` | Rename the PDU on the device |

Example:

```yaml
service: middle_atlantic_racklink.set_outlet_name
target:
  entity_id: switch.rack_pdu_outlet_3
data:
  name: "NAS"
```

## Troubleshooting

1. **Cannot connect**
   - Verify the PDU is reachable: `nc -v <pdu_ip> 443` (Redfish) or `nc -v <pdu_ip> 6000` (telnet)
   - Ensure the control protocol is enabled on the device:
     - Premium: enabled automatically after first web login and password change
     - Premium+: enable via Device Settings → Network Services → Control Protocol
2. **Authentication failed**
   - Home Assistant will prompt for reauthentication; enter the current device credentials
3. **Vendor features missing** (load shedding/sequencing buttons absent)
   - These require a telnet channel; enable vendor features, and make sure port 6000 is reachable

### Debug logging

```yaml
logger:
  logs:
    custom_components.middle_atlantic_racklink: debug
```

## Development

```bash
pip install -r requirements-test.txt
pytest tests/
mypy custom_components/middle_atlantic_racklink
```

## Contributing

Contributions are welcome! Please open a Pull Request or an Issue on GitHub.

## License

MIT — see the LICENSE file.
