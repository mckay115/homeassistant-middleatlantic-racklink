# Middle Atlantic RackLink Home Assistant Integration

![Middle Atlantic Products](custom_components/middle_atlantic_racklink/brand/logo.png)

**Requires Home Assistant 2026.10.1 or newer.** This is a community integration, not an official Legrand product. Local brand artwork ships with the integration.

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

### Before first setup

1. Open the PDU web interface in your browser and finish its initial login and password change. Use those device credentials in Home Assistant.
2. Reserve the PDU address in your router (or use a stable hostname). Enter only the address, without a URL scheme or path.
3. For Premium devices, use Telnet on TCP 6000. For Premium+ devices, prefer Redfish; for binary control, select Telnet and set port 60000, or use Auto.
4. Enable **Device Settings → Network Services → Control Protocol** on models that require it. Redfish vendor features open a second connection on port 6000; disable that option if you only need Redfish outlet controls and meters.
5. Allow Home Assistant to reach the relevant PDU ports across VLANs. mDNS discovery usually needs the same network segment; manual setup works across routed networks. No configuration.yaml entry is needed.

The connection screen accepts an optional port, HTTPS preference, and vendor-feature setting. Auto tries standard ports and stores the detected protocol for reliable reconnects. HTTPS accepts device self-signed certificates and can fall back to unencrypted HTTP; Telnet is unencrypted. Use these connections on a trusted local network.

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

Energy is exposed as **kWh**, with device class `energy` and state class `total_increasing`. Real zero readings remain zero; missing or invalid readings become unknown.

1. Open **Settings → Energy → Individual devices** and add the PDU Energy sensor for whole-rack consumption, or the outlet energy sensors for metered equipment.
2. If your Home Assistant energy configuration supports a parent device, set the PDU as the parent of its outlets. Otherwise, choose rack totals or outlet totals to avoid counting the same consumption twice.
3. Allow a recorder/statistics interval before expecting history. Keep these entities included in Recorder; check **Developer Tools → Statistics** if a meter does not appear.

Only available hardware meters can populate Energy. Legacy Telnet voltage × current is exposed as **apparent power (VA)**, not fabricated active power (W). If a device reports actual watts but no cumulative energy, create an **Integral helper** from that power sensor (W), with kilo prefix, hours, and a suitable maximum sub-interval, then add its kWh output to Energy. Do not integrate VA as though it were watts. Missing meters are left unknown.

The PDU is a consumption submeter. Do not add it as a second grid-consumption source alongside a whole-home meter unless it measures an independent grid feed.

## Dashboard cards

The integration bundles three custom Lovelace cards and registers them automatically — no manual resource configuration or extra HACS frontend install is needed. They appear in the card picker ("RackLink PDU", "RackLink sequencer", and "RackLink energy") with a visual editor, or can be added via YAML:

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

```yaml
type: custom:racklink-energy-card
device_id: YOUR_PDU_DEVICE_ID
```

The **energy card** compares live outlet power and shows cumulative kWh. These bars represent current power distribution, not historical consumption. For history, use Home Assistant's built-in **Statistics graph**, **History graph**, and **Energy** cards. Add your PDU and outlet meters to the Energy configuration first.

Controls disable while an action is pending or its entity is unavailable. Failed actions show an error. Power-off, outlet cycling, and load-shedding activation require confirmation in the bundled cards. API calls and automations still execute directly: shut down servers and storage cleanly before cutting their power.

Optional cards to consider: **ApexCharts Card** for detailed power trends and comparisons, and **Mushroom** for compact generic entity controls. They require separate frontend installation and are not bundled dependencies. Start with the included cards and built-in history cards; add these only if you need their styling or chart features.

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
   - These require a telnet channel; enable vendor features in **Reconfigure**, and make sure Control Protocol on port 6000 is reachable

### Debug logging

```yaml
logger:
  logs:
    custom_components.middle_atlantic_racklink: debug
```

## Updating and rollback

Back up Home Assistant before updating. Install the release through HACS (or replace the integration folder manually), then restart Home Assistant and refresh your browser. Existing entity IDs and history are preserved; do not delete and re-add the integration. Brand images live in the supported `brand/` directory, and the bundled cards use versioned URLs to refresh browser caches.

For a manual install, extract the release ZIP into `config/custom_components/middle_atlantic_racklink/`, with `manifest.json` directly inside that directory. The ZIP contains the integration files, not an extra enclosing folder. If you need to roll back, restore the previous version through HACS or your backup and restart.

Releases are published after the version bump on `main` passes release tests. Repository publication does not install code or upgrade Home Assistant on your server.

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
