# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-07-03

### Added
- Bundled custom dashboard cards, loaded automatically (no manual resource setup):
  - `custom:racklink-pdu-card`: PDU overview with live power metrics and per-outlet switch/cycle controls
  - `custom:racklink-sequencer-card`: outlet sequencing, sequence delay, and load shedding management
- `outlet_number` attribute on per-outlet sensors, binary sensors, and cycle buttons (used by the cards to group entities per outlet)

### Changed
- Renamed the per-outlet "sheds on load shedding" sensor to "powers off during load shedding" to avoid misreading it as the load shedding mode state
- CI/release workflows use Node 24 action runtimes (`actions/checkout@v6`, `actions/setup-python@v6`, `softprops/action-gh-release@v3`)

## [1.1.0] - 2026-07-03

### Added
- Reauthentication flow: Home Assistant prompts for new credentials when the device rejects them
- Reconfiguration flow: change host, credentials, or connection type without deleting the entry
- Working zeroconf discovery flow that completes pairing and supports multiple PDUs
- Entity services `cycle_outlet`, `cycle_all_outlets`, `set_outlet_name`, and `set_pdu_name` (previously documented but not implemented)
- Diagnostics support with redacted credentials
- Translations (`translations/en.json`), `icons.json`, and translated entity names
- Dynamic outlet discovery: entities are created from real device data and added when new outlets appear
- Load shedding and outlet sequence switches (replacing the start/stop button pairs and status binary sensors)
- Outlet sequence delay number entity (1-60 s, persisted in the config entry options and applied when sequencing starts)
- `SwitchDeviceClass.OUTLET` on outlet switches and `suggested_display_precision` on all measurement sensors

### Changed
- Migrated to `entry.runtime_data` with a typed coordinator and `DeviceInfo`
- Sensors rewritten with `SensorEntityDescription`; missing readings report `unknown` instead of `0`
- Energy readings are stored in Wh internally and exposed in kWh without heuristics (fixes corrupted long-term statistics)
- Binary sensors now update through the data update coordinator
- Surge protection is exposed as a `problem` binary sensor (on = protection lost)
- Per-outlet voltage sensors are disabled by default (they duplicate the mains voltage)
- Options changes apply the polling interval in place instead of reloading the entry
- Release workflow is tag-driven and validates the tag against the manifest version

### Fixed
- Redfish auto-detection port bug that broke auto mode
- Binary-protocol ping task racing command reads on the same stream
- Telnet disconnects now mark the connection lost so reconnection works
- aiohttp session leaks on reconnect
- Outlet sensor unique IDs (`unknown_*`) migrated to serial-based IDs with history preserved
- Default port mismatch (60000 vs 6000) in setup
- `cycle_all_outlets` no longer treats failed responses as success

### Removed
- ~2,700 lines of dead code (unused controller stack, parser, debug scripts)
- Hardcoded 8-outlet fallback
- Pre-filled default password in the config flow
- Plaintext credential logging

## [1.0.0] - 2024-01-XX

### Added - Major Release: Redfish API Support & Comprehensive Power Monitoring

#### 🚀 New Connection Types
- **Redfish REST API Support**: Modern, secure HTTPS communication
- **Hybrid Mode**: Combine Redfish efficiency with Telnet vendor features
- **Auto-Detection**: Intelligent protocol selection with fallback
- **Connection Factory Pattern**: Clean architecture for multi-protocol support

#### ⚡ Power Monitoring Revolution
- **Comprehensive PDU Classification**: Proper Home Assistant device identification
- **System Power Sensors**: Real power, apparent power, voltage, current, frequency, power factor
- **Energy Tracking**: Native kWh integration with HA energy dashboard
- **Individual Outlet Monitoring**: Per-outlet power/energy/current/voltage (Redfish mode)
- **6x Faster Updates**: 10-second intervals with Redfish vs 60-second Telnet

#### 🏠 Home Assistant Integration
- **Energy Dashboard**: Native integration with HA energy tracking
- **Proper Device Classes**: All sensors use official HA device classes
- **Smart Update Intervals**: Connection-aware frequency optimization
- **Professional Classification**: PDU identified as electrical equipment
- **Configuration URL**: Direct device web interface access

#### 🔧 Technical Improvements
- **Enhanced Error Handling**: Comprehensive logging and graceful degradation
- **Type Safety**: Full mypy type checking support
- **Test Coverage**: Comprehensive test suite with CI/CD
- **Code Quality**: Black formatting, isort, flake8, pylint
- **Security**: Bandit security scanning

#### 📊 New Sensors (All Connection Types)
- Total Power (W) - Real power consumption
- Total Energy (kWh) - Cumulative energy usage
- Apparent Power (VA) - Total power including reactive
- Power Factor - Efficiency ratio (0-1)
- RMS Voltage (V) - AC voltage measurement
- RMS Current (A) - AC current measurement
- Line Frequency (Hz) - AC frequency monitoring

#### 🔌 Individual Outlet Sensors (Redfish Only)
- Outlet X Power (W) - Per-outlet power consumption
- Outlet X Energy (kWh) - Per-outlet energy tracking
- Outlet X Current (A) - Per-outlet current draw
- Outlet X Voltage (V) - Per-outlet voltage measurement

#### 🛠️ Configuration Enhancements
- **Connection Type Selection**: Auto-detect, Redfish, Telnet options
- **Vendor Features Toggle**: Enable/disable load shedding & sequencing
- **HTTPS/HTTP Support**: Secure and standard communication modes
- **Smart Defaults**: Optimal settings chosen automatically
- **User-Friendly Setup**: Clear descriptions and validation

### Changed
- **Minimum HA Version**: Updated to 2023.1.0 for modern features
- **Update Intervals**: Smart defaults (10s Redfish, 60s Telnet)
- **Device Classification**: Enhanced PDU identification
- **Error Messages**: More descriptive and actionable feedback
- **Dependencies**: Added aiohttp for Redfish support

### Fixed
- **Session Corruption**: Eliminated with Redfish stateless connections
- **Connection Reliability**: Improved error handling and reconnection
- **Memory Leaks**: Better resource management
- **State Synchronization**: More accurate outlet state tracking

### Security
- **HTTPS Support**: Secure Redfish communication
- **Input Validation**: Enhanced parameter checking
- **Credential Handling**: Improved security practices
- **Dependency Updates**: Latest secure versions

## [0.4.0] - Previous Release
- Legacy Telnet/Binary protocol support
- Basic outlet control and monitoring
- Load shedding and sequencing features
- Initial HACS support

---

## Migration Guide from 0.4.x to 1.0.0

### Automatic Migration
- Existing Telnet configurations will continue working unchanged
- No breaking changes to existing automation or entity IDs
- All current features remain available

### Recommended Upgrades
1. **Switch to Redfish**: Enable modern API for faster updates
2. **Enable Hybrid Mode**: Get best of both worlds
3. **Add to Energy Dashboard**: Leverage new power monitoring features
4. **Update Automations**: Use new power sensors for advanced control

### New Installation
1. Install via HACS (recommended)
2. Choose connection type during setup:
   - **Auto-detect**: Let the integration choose (recommended)
   - **Redfish**: Modern REST API (fast, secure)
   - **Telnet**: Legacy protocol (stable, vendor features)
3. Enable vendor features if needed (load shedding, sequencing)
4. Add to Home Assistant Energy Dashboard

For detailed setup instructions, see the [README](README.md).