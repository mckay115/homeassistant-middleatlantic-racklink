#!/usr/bin/env python3
"""Debug script to test the validate_connection function directly."""

import asyncio
import sys
import logging

# Add the custom components path
sys.path.append('custom_components')

from middle_atlantic_racklink.const import (
    CONF_HOST, CONF_PORT, CONF_USERNAME, CONF_PASSWORD, 
    CONF_CONNECTION_TYPE, CONF_USE_HTTPS, CONF_ENABLE_VENDOR_FEATURES,
    CONNECTION_TYPE_TELNET, CONNECTION_TYPE_REDFISH
)

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def test_validation(host, username, password, protocol):
    """Test the validate_connection function directly."""
    
    # Mock a minimal HomeAssistant object
    class MockHass:
        pass
    
    hass = MockHass()
    
    if protocol == "telnet":
        data = {
            CONF_HOST: host,
            CONF_PORT: 6000,
            CONF_USERNAME: username,
            CONF_PASSWORD: password,
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_TELNET,
            CONF_USE_HTTPS: False,
            CONF_ENABLE_VENDOR_FEATURES: True,
        }
    else:  # redfish
        data = {
            CONF_HOST: host,
            CONF_PORT: 80,  # Start with HTTP
            CONF_USERNAME: username,
            CONF_PASSWORD: password,
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_REDFISH,
            CONF_USE_HTTPS: False,
            CONF_ENABLE_VENDOR_FEATURES: True,
        }
    
    logger.info(f"Testing validation with data: {data}")
    
    try:
        # Import and call the validate_connection function
        from middle_atlantic_racklink.config_flow import validate_connection
        
        result = await validate_connection(hass, data)
        logger.info(f"✅ Validation successful: {result}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Validation failed: {e}")
        logger.exception("Full traceback:")
        return False

async def main():
    """Main test function."""
    if len(sys.argv) < 5:
        print("Usage: python debug_validation.py <host> <username> <password> <telnet|redfish>")
        print("Example: python debug_validation.py 10.0.1.211 admin slot6.wrk telnet")
        sys.exit(1)
    
    host = sys.argv[1]
    username = sys.argv[2]
    password = sys.argv[3]
    protocol = sys.argv[4].lower()
    
    if protocol not in ["telnet", "redfish"]:
        print("Protocol must be 'telnet' or 'redfish'")
        sys.exit(1)
    
    success = await test_validation(host, username, password, protocol)
    
    if success:
        print("🎉 Validation test PASSED")
    else:
        print("💥 Validation test FAILED")

if __name__ == "__main__":
    asyncio.run(main())