#!/usr/bin/env python3
"""Debug script to test RackLink connection outside of Home Assistant."""

import asyncio
import sys
import logging

# Add the custom components path
sys.path.append('custom_components')

from middle_atlantic_racklink.controller.racklink_controller import RacklinkController
from middle_atlantic_racklink.const import CONNECTION_TYPE_TELNET, CONNECTION_TYPE_REDFISH

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def test_connection(host, port, username, password, connection_type, use_https=False):
    """Test connection with given parameters."""
    logger.info(f"Testing {connection_type} connection to {host}:{port}")
    
    try:
        controller = RacklinkController(
            host=host,
            port=port,
            username=username,
            password=password,
            timeout=20,
            connection_type=connection_type,
            use_https=use_https,
            enable_vendor_features=True,
        )
        logger.info("✅ Controller created successfully")
        
        # Try to connect
        logger.info("Attempting to connect...")
        connected = await controller.connect()
        
        if connected:
            logger.info("✅ Connection successful!")
            
            # Try to get basic info
            await controller.update()
            
            logger.info(f"PDU Name: {controller.pdu_name}")
            logger.info(f"PDU Model: {controller.pdu_model}")
            logger.info(f"MAC Address: {controller.mac_address}")
            
            # Test basic communication
            if hasattr(controller.connection, 'send_command'):
                logger.info("Testing telnet command...")
                result = await controller.connection.send_command("help")
                logger.info(f"Help command result: {result[:100]}...")
            
            await controller.disconnect()
            return True
        else:
            logger.error("❌ Failed to connect")
            return False
            
    except Exception as e:
        logger.error(f"❌ Exception during test: {e}")
        logger.exception("Full traceback:")
        return False

async def main():
    """Main test function."""
    if len(sys.argv) < 5:
        print("Usage: python debug_connection.py <host> <username> <password> <telnet|redfish>")
        print("Example: python debug_connection.py 10.0.1.211 admin slot6.wrk telnet")
        sys.exit(1)
    
    host = sys.argv[1]
    username = sys.argv[2]
    password = sys.argv[3]
    protocol = sys.argv[4].lower()
    
    if protocol == "telnet":
        success = await test_connection(host, 6000, username, password, CONNECTION_TYPE_TELNET)
    elif protocol == "redfish":
        # Try HTTP first
        logger.info("Trying Redfish HTTP...")
        success = await test_connection(host, 80, username, password, CONNECTION_TYPE_REDFISH, use_https=False)
        
        if not success:
            logger.info("HTTP failed, trying HTTPS...")
            success = await test_connection(host, 443, username, password, CONNECTION_TYPE_REDFISH, use_https=True)
    else:
        print("Protocol must be 'telnet' or 'redfish'")
        sys.exit(1)
    
    if success:
        print("🎉 Connection test PASSED")
    else:
        print("💥 Connection test FAILED")

if __name__ == "__main__":
    asyncio.run(main())