"""Redfish REST API connection handler for Middle Atlantic RackLink devices."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urljoin

import aiohttp

_LOGGER = logging.getLogger(__name__)

# Redfish API endpoints
REDFISH_SERVICE_ROOT = "/redfish/v1/"
REDFISH_CHASSIS_ENDPOINT = "/redfish/v1/Chassis"
REDFISH_POWER_EQUIPMENT = "/redfish/v1/PowerEquipment"
REDFISH_SESSION_SERVICE = "/redfish/v1/SessionService/Sessions"

# Middle Atlantic specific endpoints (based on device testing)
PDU_OUTLETS_ENDPOINT = "/redfish/v1/PowerEquipment/RackPDUs"
PDU_POWER_CONTROL = "/redfish/v1/PowerEquipment/RackPDUs/{pdu_id}/Outlets"


@dataclass
class RedfishConfig:
    """Configuration for Redfish connection."""

    host: str
    port: int = 443
    username: Optional[str] = None
    password: Optional[str] = None
    use_https: bool = True
    timeout: int = 20
    verify_ssl: bool = False  # Many PDUs use self-signed certificates


class RedfishConnection:
    """Redfish REST API connection manager for Middle Atlantic RackLink devices."""

    def __init__(self, config: RedfishConfig) -> None:
        """Initialize the Redfish connection.

        Args:
            config: Configuration object containing connection details
        """
        self.config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._auth_token: Optional[str] = None
        self._session_location: Optional[str] = None
        self._connected = False
        self._authenticated = False
        self._base_url = self._build_base_url()
        self._pdu_id: Optional[str] = None
        self._outlet_endpoints: Dict[int, str] = {}
        self._outlet_names: Dict[int, str] = {}
        self._redfish_version: Optional[str] = None

    @property
    def connected(self) -> bool:
        """Return True if connected to the device."""
        return self._connected

    @property
    def authenticated(self) -> bool:
        """Return True if authenticated with the device."""
        return self._authenticated

    def _build_base_url(self) -> str:
        """Build the base URL for Redfish API."""
        protocol = "https" if self.config.use_https else "http"
        return f"{protocol}://{self.config.host}:{self.config.port}"

    async def connect(self) -> bool:
        """Connect to the device and establish session."""
        try:
            _LOGGER.info(
                "Connecting to Redfish API at %s:%d (HTTPS: %s)",
                self.config.host,
                self.config.port,
                self.config.use_https,
            )
            _LOGGER.debug(
                "Redfish SSL verify: %s (self-signed certs will be accepted when False)",
                self.config.verify_ssl,
            )

            # Create HTTP session with appropriate SSL settings
            connector = aiohttp.TCPConnector(
                ssl=(
                    False
                    if not self.config.verify_ssl
                    else ssl.create_default_context()
                )
            )
            timeout = aiohttp.ClientTimeout(total=self.config.timeout)

            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )

            # Test basic connectivity by accessing service root
            if not await self._test_connectivity():
                await self._close_session()
                return False

            self._connected = True

            # Authenticate if credentials provided
            if self.config.username and self.config.password:
                if await self._authenticate():
                    self._authenticated = True
                    _LOGGER.info("Successfully authenticated to Redfish API")
                else:
                    _LOGGER.error("Failed to authenticate to Redfish API")
                    await self._close_session()
                    return False
            else:
                # Some PDUs might allow anonymous access for read operations
                _LOGGER.info("No credentials provided, attempting anonymous access")
                self._authenticated = True

            # Discover PDU structure
            await self._discover_pdu_structure()

            return True

        except Exception as err:
            _LOGGER.error("Error connecting to Redfish API: %s", err)
            await self._close_session()
            return False

    async def _test_connectivity(self) -> bool:
        """Test basic connectivity to Redfish service."""
        try:
            async with self._session.get(
                urljoin(self._base_url, REDFISH_SERVICE_ROOT)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    _LOGGER.debug("Redfish service root response: %s", data)
                    # Capture RedfishVersion if available
                    try:
                        self._redfish_version = data.get("RedfishVersion")
                    except Exception:
                        pass
                    return True
                else:
                    _LOGGER.error(
                        "Failed to access Redfish service root, status: %d",
                        response.status,
                    )
                    return False
        except Exception as err:
            _LOGGER.error("Error testing Redfish connectivity: %s", err)
            return False

    async def _authenticate(self) -> bool:
        """Authenticate with the Redfish service."""
        try:
            auth_data = {
                "UserName": self.config.username,
                "Password": self.config.password,
            }

            async with self._session.post(
                urljoin(self._base_url, REDFISH_SESSION_SERVICE),
                json=auth_data,
            ) as response:
                if response.status in [200, 201]:
                    # Get authentication token from response
                    self._auth_token = response.headers.get("X-Auth-Token")
                    self._session_location = response.headers.get("Location")

                    if self._auth_token:
                        # Add auth token to session headers
                        self._session.headers["X-Auth-Token"] = self._auth_token
                        _LOGGER.debug("Authentication successful, got token")
                        return True
                    else:
                        _LOGGER.warning(
                            "Authentication succeeded but no token received"
                        )
                        return True
                else:
                    _LOGGER.error("Authentication failed, status: %d", response.status)
                    return False
        except Exception as err:
            _LOGGER.error("Error during Redfish authentication: %s", err)
            return False

    async def _discover_pdu_structure(self) -> None:
        """Discover PDU structure and outlet endpoints for Middle Atlantic devices."""
        try:
            # Middle Atlantic uses RackPDUs endpoint
            rack_pdus_url = urljoin(
                self._base_url, "/redfish/v1/PowerEquipment/RackPDUs"
            )

            async with self._session.get(rack_pdus_url) as response:
                if response.status == 200:
                    data = await response.json()
                    await self._parse_rack_pdus(data)
                else:
                    _LOGGER.warning(
                        "RackPDUs endpoint not found, trying generic power equipment"
                    )
                    await self._discover_via_power_equipment()

        except Exception as err:
            _LOGGER.error("Error discovering PDU structure: %s", err)

    async def _parse_rack_pdus(self, data: Dict[str, Any]) -> None:
        """Parse RackPDUs collection to find PDU outlets."""
        try:
            # Get first PDU (assuming single PDU setup)
            if "Members" in data and data["Members"]:
                pdu_url = data["Members"][0]["@odata.id"]
                self._pdu_id = pdu_url.split("/")[-1]

                _LOGGER.info("Found PDU at: %s", pdu_url)

                # Get PDU details and outlets
                async with self._session.get(
                    urljoin(self._base_url, pdu_url)
                ) as response:
                    if response.status == 200:
                        pdu_data = await response.json()
                        await self._parse_pdu_outlets_direct(pdu_data)
        except Exception as err:
            _LOGGER.error("Error parsing RackPDUs: %s", err)

    async def _parse_pdu_collection(self, data: Dict[str, Any]) -> None:
        """Parse PDU collection to find outlets."""
        try:
            # Get first PDU (assuming single PDU setup)
            if "Members" in data and data["Members"]:
                pdu_url = data["Members"][0]["@odata.id"]
                self._pdu_id = pdu_url.split("/")[-1]

                # Get PDU details
                async with self._session.get(
                    urljoin(self._base_url, pdu_url)
                ) as response:
                    if response.status == 200:
                        pdu_data = await response.json()
                        await self._parse_pdu_outlets(pdu_data)
        except Exception as err:
            _LOGGER.error("Error parsing PDU collection: %s", err)

    async def _parse_pdu_outlets_direct(self, data: Dict[str, Any]) -> None:
        """Parse PDU data to find outlet endpoints (Middle Atlantic specific)."""
        try:
            # Look for Outlets collection
            if "Outlets" in data:
                outlets_url = data["Outlets"]["@odata.id"]
                async with self._session.get(
                    urljoin(self._base_url, outlets_url)
                ) as response:
                    if response.status == 200:
                        outlets_data = await response.json()

                        # Parse individual outlets - Middle Atlantic uses simple numbering
                        if "Members" in outlets_data:
                            for outlet_ref in outlets_data["Members"]:
                                outlet_url = outlet_ref["@odata.id"]
                                outlet_id = outlet_url.split("/")[-1]

                                # Middle Atlantic uses simple integer IDs (1, 2, 3, etc.)
                                try:
                                    outlet_num = int(outlet_id)
                                    self._outlet_endpoints[outlet_num] = outlet_url
                                except (ValueError, AttributeError):
                                    _LOGGER.warning(
                                        "Could not parse outlet number from %s",
                                        outlet_id,
                                    )

                        _LOGGER.info(
                            "Discovered %d outlets", len(self._outlet_endpoints)
                        )
        except Exception as err:
            _LOGGER.error("Error parsing PDU outlets: %s", err)

    async def _fetch_json(self, relative_url: str) -> Optional[Dict[str, Any]]:
        """Helper to fetch JSON from a Redfish relative URL safely."""
        try:
            async with self._session.get(urljoin(self._base_url, relative_url)) as resp:
                if resp.status == 200:
                    return await resp.json()
                _LOGGER.debug("GET %s returned status %d", relative_url, resp.status)
        except Exception as err:
            _LOGGER.debug("GET %s failed: %s", relative_url, err)
        return None

    async def get_all_outlets_info(self) -> Dict[int, Dict[str, Any]]:
        """Fetch info for all outlets concurrently (state, name, etc.).

        Returns a dict: {outlet_num: {"state": bool, "name": str}}
        """
        if not self._outlet_endpoints:
            return {}

        async def fetch_one(
            outlet_num: int, outlet_url: str
        ) -> Tuple[int, Dict[str, Any]]:
            data = await self._fetch_json(outlet_url)
            result: Dict[str, Any] = {}
            if data:
                # Middle Atlantic uses string PowerState: "On" or "Off"
                power_state = data.get("PowerState")
                if isinstance(power_state, str):
                    result["state"] = power_state == "On"
                # Prefer UserLabel when available for friendly naming
                label = data.get("UserLabel")
                name = (label or data.get("Name") or data.get("Id") or "").strip()
                if isinstance(name, str) and name:
                    result["name"] = name
                # Include useful attributes for entity extra attributes
                result["attrs"] = {
                    "user_label": label,
                    "power_restore_policy": data.get("PowerRestorePolicy"),
                    "power_on_delay_seconds": data.get("PowerOnDelaySeconds"),
                    "power_cycle_delay_seconds": data.get("PowerCycleDelaySeconds"),
                    "rated_current_amps": data.get("RatedCurrentAmps"),
                    "nominal_voltage": data.get("NominalVoltage"),
                }
            return outlet_num, result

        tasks = [
            fetch_one(outlet_num, outlet_url)
            for outlet_num, outlet_url in self._outlet_endpoints.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        info: Dict[int, Dict[str, Any]] = {}
        for item in results:
            if isinstance(item, Exception):
                _LOGGER.debug("Outlet info fetch error: %s", item)
                continue
            outlet_num, data = item
            if data:
                info[outlet_num] = data
                if "name" in data:
                    self._outlet_names[outlet_num] = data["name"]
        return info

    async def get_outlet_metrics(self, outlet_num: int) -> Optional[Dict[str, Any]]:
        """Fetch metrics for a specific outlet if exposed by Redfish.

        Tries either inline properties on the outlet resource or a nested
        Metrics sub-resource commonly used by Redfish PDUs.
        """
        try:
            if outlet_num not in self._outlet_endpoints:
                return None

            outlet_url = self._outlet_endpoints[outlet_num]
            data = await self._fetch_json(outlet_url)
            if not data:
                return None

            metrics: Dict[str, Any] = {}

            # Inline fields first
            # Common field names seen across vendors
            inline_map = [
                ("Power", "power"),
                ("PowerWatts", "power"),
                ("CurrentAmps", "current"),
                ("Current", "current"),
                ("Voltage", "voltage"),
                ("EnergykWh", "energy_kwh"),
                ("Energy", "energy_kwh"),
                ("FrequencyHz", "frequency"),
            ]
            # Extract apparent power and power factor when embedded in PowerWatts
            pw = data.get("PowerWatts")
            if isinstance(pw, dict):
                if "ApparentVA" in pw:
                    metrics["apparent_va"] = pw.get("ApparentVA")
                if "PowerFactor" in pw:
                    metrics["power_factor"] = pw.get("PowerFactor")
            for src, dest in inline_map:
                v = data.get(src)
                if isinstance(v, dict) and "Reading" in v:
                    metrics[dest] = v.get("Reading")
                elif isinstance(v, (int, float)):
                    metrics[dest] = v

            # Try Metrics sub-resource
            if (
                "Metrics" in data
                and isinstance(data["Metrics"], dict)
                and "@odata.id" in data["Metrics"]
            ):
                m_url = data["Metrics"]["@odata.id"]
                m_data = await self._fetch_json(m_url)
                if m_data:
                    for key, dest in [
                        ("PowerWatts", "power"),
                        ("CurrentAmps", "current"),
                        ("Voltage", "voltage"),
                        ("EnergykWh", "energy_kwh"),
                        ("FrequencyHz", "frequency"),
                    ]:
                        v = m_data.get(key)
                        if isinstance(v, dict) and "Reading" in v:
                            metrics[dest] = v.get("Reading")
                    pw2 = m_data.get("PowerWatts")
                    if isinstance(pw2, dict):
                        if "ApparentVA" in pw2:
                            metrics["apparent_va"] = pw2.get("ApparentVA")
                        if "PowerFactor" in pw2:
                            metrics["power_factor"] = pw2.get("PowerFactor")

            if metrics:
                return metrics
        except Exception as err:
            _LOGGER.debug("Outlet %d metrics fetch failed: %s", outlet_num, err)
        return None

    async def get_all_outlets_metrics(self) -> Dict[int, Dict[str, Any]]:
        """Fetch metrics for all outlets concurrently, if available."""
        tasks = [self.get_outlet_metrics(n) for n in self._outlet_endpoints.keys()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: Dict[int, Dict[str, Any]] = {}
        for (outlet_num, _), res in zip(self._outlet_endpoints.items(), results):
            if isinstance(res, dict) and res:
                out[outlet_num] = res
        return out

    @property
    def redfish_version(self) -> Optional[str]:
        return self._redfish_version

    async def _discover_via_power_equipment(self) -> None:
        """Fallback discovery via generic PowerEquipment endpoint."""
        try:
            power_equipment_url = urljoin(self._base_url, REDFISH_POWER_EQUIPMENT)
            async with self._session.get(power_equipment_url) as response:
                if response.status == 200:
                    data = await response.json()
                    # Look for PowerDistribution as fallback
                    if "PowerDistribution" in data:
                        pdu_collection_url = data["PowerDistribution"]["@odata.id"]
                        async with self._session.get(
                            urljoin(self._base_url, pdu_collection_url)
                        ) as response:
                            if response.status == 200:
                                pdu_data = await response.json()
                                await self._parse_pdu_collection_generic(pdu_data)
                    else:
                        _LOGGER.warning("No PowerDistribution found in PowerEquipment")
        except Exception as err:
            _LOGGER.error("Error in power equipment discovery: %s", err)

    async def _parse_pdu_collection_generic(self, data: Dict[str, Any]) -> None:
        """Parse generic PDU collection (fallback method)."""
        try:
            if "Members" in data and data["Members"]:
                pdu_url = data["Members"][0]["@odata.id"]
                self._pdu_id = pdu_url.split("/")[-1]

                async with self._session.get(
                    urljoin(self._base_url, pdu_url)
                ) as response:
                    if response.status == 200:
                        pdu_data = await response.json()
                        await self._parse_pdu_outlets_direct(pdu_data)
        except Exception as err:
            _LOGGER.error("Error parsing generic PDU collection: %s", err)

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        try:
            # Logout if we have a session
            if self._session_location and self._session:
                try:
                    async with self._session.delete(
                        urljoin(self._base_url, self._session_location)
                    ) as response:
                        _LOGGER.debug("Logout response: %d", response.status)
                except Exception as err:
                    _LOGGER.debug("Error during logout: %s", err)

            await self._close_session()

        except Exception as err:
            _LOGGER.error("Error disconnecting from Redfish API: %s", err)
        finally:
            self._connected = False
            self._authenticated = False
            self._auth_token = None
            self._session_location = None

    async def _close_session(self) -> None:
        """Close the HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None

    async def get_pdu_info(self) -> Dict[str, Any]:
        """Get PDU information from Middle Atlantic device."""
        try:
            if not self._pdu_id:
                return {}

            # Middle Atlantic uses RackPDUs endpoint
            pdu_url = f"/redfish/v1/PowerEquipment/RackPDUs/{self._pdu_id}"
            async with self._session.get(urljoin(self._base_url, pdu_url)) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    _LOGGER.error("Failed to get PDU info, status: %d", response.status)
                    return {}
        except Exception as err:
            _LOGGER.error("Error getting PDU info: %s", err)
            return {}

    async def get_outlet_state(self, outlet_num: int) -> Optional[bool]:
        """Get the state of a specific outlet (Middle Atlantic specific)."""
        try:
            if outlet_num not in self._outlet_endpoints:
                _LOGGER.error("Outlet %d not found in discovered outlets", outlet_num)
                return None

            outlet_url = self._outlet_endpoints[outlet_num]
            async with self._session.get(
                urljoin(self._base_url, outlet_url)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    # Middle Atlantic uses string PowerState: "On" or "Off"
                    power_state = data.get("PowerState", "Unknown")
                    return power_state == "On"  # Exact string match, case-sensitive
                else:
                    _LOGGER.error(
                        "Failed to get outlet %d state, status: %d",
                        outlet_num,
                        response.status,
                    )
                    return None
        except Exception as err:
            _LOGGER.error("Error getting outlet %d state: %s", outlet_num, err)
            return None

    async def set_outlet_state(self, outlet_num: int, state: bool) -> bool:
        """Set the state of a specific outlet (Middle Atlantic specific)."""
        try:
            if outlet_num not in self._outlet_endpoints:
                _LOGGER.error("Outlet %d not found in discovered outlets", outlet_num)
                return False

            # Middle Atlantic uses PowerControl action endpoint
            outlet_url = self._outlet_endpoints[outlet_num]
            action_url = f"{outlet_url}/Actions/Outlet.PowerControl"

            # Action payload with PowerState
            action_data = {"PowerState": "On" if state else "Off"}

            async with self._session.post(
                urljoin(self._base_url, action_url),
                json=action_data,
            ) as response:
                if response.status in [200, 202, 204]:
                    _LOGGER.info(
                        "Successfully set outlet %d to %s",
                        outlet_num,
                        "ON" if state else "OFF",
                    )
                    return True
                else:
                    _LOGGER.error(
                        "Failed to set outlet %d state, status: %d",
                        outlet_num,
                        response.status,
                    )
                    return False
        except Exception as err:
            _LOGGER.error("Error setting outlet %d state: %s", outlet_num, err)
            return False

    async def cycle_outlet(self, outlet_num: int) -> bool:
        """Cycle (power off then on) a specific outlet (Middle Atlantic specific)."""
        try:
            if outlet_num not in self._outlet_endpoints:
                _LOGGER.error("Outlet %d not found in discovered outlets", outlet_num)
                return False

            # Middle Atlantic supports PowerCycle via PowerControl action
            outlet_url = self._outlet_endpoints[outlet_num]
            action_url = f"{outlet_url}/Actions/Outlet.PowerControl"

            # Try PowerCycle command
            action_data = {"PowerState": "PowerCycle"}

            async with self._session.post(
                urljoin(self._base_url, action_url),
                json=action_data,
            ) as response:
                if response.status in [200, 202, 204]:
                    _LOGGER.info("Successfully cycled outlet %d", outlet_num)
                    return True
                else:
                    # Fallback to manual off/on sequence
                    _LOGGER.debug(
                        "PowerCycle not supported, using manual off/on sequence"
                    )
                    if await self.set_outlet_state(outlet_num, False):
                        await asyncio.sleep(2)  # Wait 2 seconds
                        return await self.set_outlet_state(outlet_num, True)
                    return False
        except Exception as err:
            _LOGGER.error("Error cycling outlet %d: %s", outlet_num, err)
            return False

    async def get_all_outlet_states(self) -> Dict[int, bool]:
        """Get states of all outlets."""
        # Prefer concurrent fetch via outlet info when available
        info = await self.get_all_outlets_info()
        if info:
            return {num: data.get("state", False) for num, data in info.items()}

        # Fallback to sequential state queries
        outlet_states: Dict[int, bool] = {}
        for outlet_num in self._outlet_endpoints:
            state = await self.get_outlet_state(outlet_num)
            if state is not None:
                outlet_states[outlet_num] = state
        return outlet_states

    async def get_power_metrics(self) -> Dict[str, float]:
        """Get power metrics from the Middle Atlantic PDU."""
        try:
            if not self._pdu_id:
                return {}

            # Middle Atlantic metrics endpoint
            metrics_url = f"/redfish/v1/PowerEquipment/RackPDUs/{self._pdu_id}/Metrics"
            async with self._session.get(
                urljoin(self._base_url, metrics_url)
            ) as response:
                if response.status == 200:
                    data = await response.json()

                    # Extract Middle Atlantic specific metrics structure
                    metrics = {}

                    # Energy data
                    if "EnergykWh" in data and "Reading" in data["EnergykWh"]:
                        metrics["energy"] = data["EnergykWh"]["Reading"]

                    # Power data (complex structure)
                    if "PowerWatts" in data:
                        power_data = data["PowerWatts"]
                        if "Reading" in power_data:
                            metrics["power"] = power_data["Reading"]
                        if "ApparentVA" in power_data:
                            metrics["apparent_power"] = power_data["ApparentVA"]
                        if "PowerFactor" in power_data:
                            metrics["power_factor"] = power_data["PowerFactor"]

                    return metrics
                else:
                    _LOGGER.debug("Metrics endpoint not available")
                    return {}
        except Exception as err:
            _LOGGER.error("Error getting power metrics: %s", err)
            return {}

    def get_outlet_count(self) -> int:
        """Get the number of discovered outlets."""
        return len(self._outlet_endpoints)

    async def get_mains_metrics(self) -> Dict[str, float]:
        """Get mains (inlet) metrics using Redfish Mains endpoint.

        Returns keys: voltage, current, frequency, power, apparent_power, power_factor, energy (kWh)
        """
        try:
            if not self._pdu_id:
                return {}

            # Fetch mains collection to find first circuit (e.g., I1)
            mains_coll = f"/redfish/v1/PowerEquipment/RackPDUs/{self._pdu_id}/Mains"
            mains_data = await self._fetch_json(mains_coll)
            if (
                not mains_data
                or "Members" not in mains_data
                or not mains_data["Members"]
            ):
                return {}

            circuit_url = mains_data["Members"][0].get("@odata.id")
            if not circuit_url:
                return {}

            circuit = await self._fetch_json(circuit_url)
            if not circuit:
                return {}

            def read_val(obj: Any, key: str) -> Optional[float]:
                val = obj.get(key)
                if isinstance(val, dict) and "Reading" in val:
                    return val.get("Reading")
                if isinstance(val, (int, float)):
                    return float(val)
                return None

            result: Dict[str, float] = {}
            v = read_val(circuit, "Voltage")
            if v is not None:
                result["voltage"] = float(v)
            i = read_val(circuit, "CurrentAmps")
            if i is not None:
                result["current"] = float(i)
            f = read_val(circuit, "FrequencyHz")
            if f is not None:
                result["frequency"] = float(f)
            pw = circuit.get("PowerWatts")
            if isinstance(pw, dict):
                if "Reading" in pw:
                    result["power"] = float(pw["Reading"])  # Watts
                if "ApparentVA" in pw:
                    result["apparent_power"] = float(pw["ApparentVA"])  # VA
                if "PowerFactor" in pw and pw["PowerFactor"] is not None:
                    result["power_factor"] = float(pw["PowerFactor"])  # ratio
            e = read_val(circuit, "EnergykWh")
            if e is not None:
                result["energy"] = float(e)  # kWh

            return result
        except Exception as err:
            _LOGGER.debug("Error getting mains metrics: %s", err)
            return {}
