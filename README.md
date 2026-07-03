# Middle Atlantic RackLink Home Assistant Integration

Control and monitor Middle Atlantic (Legrand) RackLink PDUs from Home Assistant.

**Note:** This integration supports Premium and Premium+ Series PDUs with control protocol capabilities.

## Features

- Outlet control: turn outlets on/off and cycle power (per outlet or all at once)
- Power monitoring: voltage, current, power, apparent power, power factor, frequency, and energy (with Home Assistant Energy dashboard support)
- Per-outlet power/energy/current/voltage sensors (Redfish mode)
- Load shedding and outlet sequencing switches with configurable sequence delay (vendor features via telnet)
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

- **Switches**: one per outlet (device class *outlet*), with outlet metadata (power-on delays, rated current) as attributes; plus **Load shedding** and **Outlet sequence** switches when a telnet channel is available
- **Sensors** (PDU): voltage, current, power, apparent power, power factor, frequency, energy
- **Sensors** (per outlet, Redfish): power, energy, current, voltage (voltage is disabled by default since it duplicates the mains voltage)
- **Binary sensors**: surge protection problem, and a per-outlet "powers off during load shedding" flag (on = the PDU has marked the outlet non-critical, so it sheds when load shedding is activated)
- **Buttons**: cycle per outlet, cycle all outlets
- **Numbers**: outlet sequence delay (seconds between outlets during a power-on sequence)

### Energy dashboard

The PDU **Energy** sensor and the per-outlet **Outlet N energy** sensors are total-increasing kWh sensors, so they can be added to the Home Assistant Energy dashboard under **Individual devices** to track rack-level and per-device consumption. The power sensors (W) feed the power graphs and can drive automations (e.g. shut down a server when its outlet draws less than a threshold).

## Dashboard cards

The integration bundles two custom Lovelace cards and registers them automatically — no manual resource configuration or extra HACS frontend install is needed. Both appear in the card picker ("RackLink PDU" and "RackLink sequencer") with a visual editor, or can be added via YAML:

```yaml
type: custom:racklink-pdu-card
device_id: YOUR_PDU_DEVICE_ID   # pick the device in the visual editor
title: Rack PDU                 # optional
```

The **PDU card** shows live voltage, current, energy, and power factor chips with the total power headline, plus one row per outlet with its state, power draw, a cycle button, and an on/off toggle. Outlets marked non-critical are tagged "sheds".

```yaml
type: custom:racklink-sequencer-card
device_id: YOUR_PDU_DEVICE_ID
```

The **sequencer card** manages power-on sequencing (enable/disable plus a delay stepper), load shedding (with the list of outlets that will power off), and a cycle-all-outlets action. Sequencing and load shedding rows appear only when the telnet vendor channel is available.

Cards discover the PDU's entities through the device registry, so they keep working if you rename entities.

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
3. **Vendor features missing** (load shedding/sequencing switches absent)
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
