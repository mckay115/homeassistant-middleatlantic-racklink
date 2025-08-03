# Redfish API Integration for Middle Atlantic RackLink

## Overview

This implementation adds Redfish REST API support to the existing Middle Atlantic RackLink Home Assistant integration, giving users a choice between modern Redfish API and legacy Telnet/Binary protocols during setup.

## What is Redfish?

Redfish is a modern, RESTful API standard developed by DMTF (Distributed Management Task Force) for managing data center equipment, including power distribution units (PDUs). It offers several advantages over legacy protocols:

- **Modern REST API**: Uses HTTP/HTTPS with JSON payloads
- **Better Security**: Proper authentication and encryption support
- **Industry Standard**: Widely adopted across data center equipment
- **Easier Integration**: Standard HTTP methods and JSON responses
- **Future-Proof**: Actively developed and maintained standard

## Implementation Features

### 1. Connection Type Selection
- **Auto-detect**: Tries Redfish first, falls back to Telnet (recommended default)
- **Redfish**: Use modern REST API (HTTPS on port 443, HTTP on port 80)
- **Telnet**: Use legacy Telnet/Binary protocols (ports 6000/60000)

### 2. Setup Wizard Enhancements
- Added connection type selection step before device configuration
- Dynamic port assignment based on connection type
- HTTPS/HTTP option for Redfish connections
- Clear descriptions of each connection method

### 3. Dual Protocol Support
- **RedfishConnection**: New class for REST API communication
- **SocketConnection**: Existing Telnet/Binary protocol support
- **ConnectionFactory**: Factory pattern to create appropriate connection type
- **AutoConnectionManager**: Smart detection of best available protocol

### 4. Controller Updates
- Updated `RacklinkController` to work with both connection types
- Automatic fallback from Redfish to Telnet when auto-detection is used
- Unified interface for outlet control regardless of underlying protocol

## Files Added/Modified

### New Files
- `redfish_connection.py`: Redfish REST API implementation
- `connection_factory.py`: Factory pattern for connection creation

### Modified Files
- `const.py`: Added Redfish-related constants and connection type definitions
- `config_flow.py`: Enhanced setup wizard with connection type selection
- `strings.json`: Updated UI text and error messages
- `controller/racklink_controller.py`: Updated to support both connection types

## Configuration Options

### Connection Types
```python
CONNECTION_TYPE_REDFISH = "redfish"     # Modern REST API
CONNECTION_TYPE_TELNET = "telnet"       # Legacy protocols
CONNECTION_TYPE_AUTO = "auto"           # Smart detection (default)
```

### Default Ports
- **Redfish HTTPS**: 443
- **Redfish HTTP**: 80
- **Telnet**: 6000 (Premium/Select series)
- **Binary**: 60000 (Premium+ series)

## Setup Flow

1. **Connection Type Selection**: User chooses between Redfish, Telnet, or Auto-detect
2. **Device Discovery**: Automatic discovery of RackLink devices on network
3. **Configuration**: Enter device details (IP, credentials, port)
4. **Validation**: Test connection with selected protocol
5. **Auto-fallback**: If using auto-detect and Redfish fails, try Telnet
6. **Integration Creation**: Create Home Assistant integration entry

## Redfish API Endpoints

The implementation uses standard Redfish endpoints:

- **Service Root**: `/redfish/v1/`
- **Power Equipment**: `/redfish/v1/PowerEquipment/PowerDistribution`
- **Outlets**: `/redfish/v1/PowerEquipment/PowerDistribution/{pdu_id}/Outlets`
- **Session Management**: `/redfish/v1/SessionService/Sessions`

## Benefits of This Implementation

### For Users
- **Choice**: Can use modern Redfish API or stick with legacy protocols
- **Future-Proof**: Ready for newer devices that support Redfish
- **Better Security**: HTTPS encryption when using Redfish
- **Easier Setup**: Auto-detection finds the best connection method

### For Developers
- **Maintainable**: Clean separation between connection types
- **Extensible**: Easy to add more connection types in the future
- **Standard**: Uses industry-standard Redfish specification
- **Backward Compatible**: Existing Telnet/Binary support unchanged

## Usage Examples

### Auto-Detection (Recommended)
The integration will automatically try Redfish first, then fall back to Telnet:

```python
controller = RacklinkController(
    host="192.168.1.100",
    port=443,  # Will be auto-adjusted
    username="admin",
    password="password",
    connection_type=CONNECTION_TYPE_AUTO,
    use_https=True
)
```

### Explicit Redfish
For devices known to support Redfish:

```python
controller = RacklinkController(
    host="192.168.1.100",
    port=443,
    username="admin", 
    password="password",
    connection_type=CONNECTION_TYPE_REDFISH,
    use_https=True
)
```

### Legacy Telnet
For older devices or when Redfish is not available:

```python
controller = RacklinkController(
    host="192.168.1.100",
    port=6000,
    username="admin",
    password="password", 
    connection_type=CONNECTION_TYPE_TELNET,
)
```

## Testing

The implementation includes comprehensive error handling and testing:

- Connection validation for both protocols
- Automatic protocol detection and fallback
- Error reporting with helpful messages
- Device capability discovery

## Future Enhancements

1. **Enhanced Redfish Features**: Support for advanced Redfish capabilities like event subscriptions
2. **Device-Specific Optimizations**: Optimize for specific Middle Atlantic models
3. **Performance Monitoring**: Add metrics for connection performance
4. **Bulk Operations**: Support for bulk outlet operations via Redfish

## Compatibility

- **Backward Compatible**: Existing Telnet/Binary integrations continue to work
- **Forward Compatible**: Ready for future Redfish-enabled devices
- **Home Assistant**: Compatible with current Home Assistant architecture
- **Middle Atlantic**: Supports all RackLink PDU models

## Security Considerations

- **HTTPS Support**: Encrypted communication for Redfish connections
- **Certificate Validation**: Configurable SSL certificate verification
- **Session Management**: Proper authentication token handling
- **Credential Protection**: Secure storage of authentication credentials

This implementation successfully modernizes the Middle Atlantic RackLink integration while maintaining full backward compatibility and providing users with the flexibility to choose the best connection method for their specific setup.