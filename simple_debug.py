#!/usr/bin/env python3
"""Simple debug script to test RackLink controller directly."""

import asyncio
import sys
import logging

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def test_controller():
    """Test the controller directly without Home Assistant dependencies."""
    
    # Add the custom components path
    sys.path.append('custom_components')
    
    try:
        # Test just the imports first
        logger.info("Testing imports...")
        
        from middle_atlantic_racklink.const import (
            CONNECTION_TYPE_TELNET, CONNECTION_TYPE_REDFISH
        )
        logger.info("✅ Constants imported successfully")
        
        from middle_atlantic_racklink.controller.racklink_controller import RacklinkController
        logger.info("✅ RacklinkController imported successfully")
        
        # Test creating a controller
        logger.info("Creating controller...")
        controller = RacklinkController(
            host="10.0.1.211",
            port=6000,
            username="admin",
            password="slot6.wrk",
            timeout=20,
            connection_type=CONNECTION_TYPE_TELNET,
            use_https=False,
            enable_vendor_features=True,
        )
        logger.info("✅ Controller created successfully")
        
        # Test connection
        logger.info("Testing connection...")
        connected = await controller.connect()
        
        if connected:
            logger.info("✅ Connected successfully!")
            
            # Test basic communication
            await controller.update()
            logger.info(f"PDU Name: {controller.pdu_name}")
            logger.info(f"Model: {controller.pdu_model}")
            
            await controller.disconnect()
            logger.info("✅ Disconnected successfully")
            return True
        else:
            logger.error("❌ Failed to connect")
            return False
            
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        logger.exception("Full traceback:")
        return False

async def main():
    """Main function."""
    success = await test_controller()
    
    if success:
        print("🎉 Test PASSED")
    else:
        print("💥 Test FAILED")

if __name__ == "__main__":
    asyncio.run(main())