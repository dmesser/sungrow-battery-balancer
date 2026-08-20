"""Modbus TCP write-only controller for Sungrow Hybrid Inverters."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

from .config import InverterHost
from .state import power_kw_to_register_value

logger = logging.getLogger(__name__)

# In pymodbus 3.x, device_id (or slave in older versions) is accepted
_write_accepts_device_id = True
try:
    import inspect

    sig = inspect.signature(ModbusTcpClient.write_register)
    _write_accepts_device_id = "device_id" in sig.parameters
except (AttributeError, TypeError, ValueError):  # pragma: no cover
    _write_accepts_device_id = True


class ModbusError(Exception):
    """Base exception for Modbus operations."""


class ModbusWriteError(ModbusError):
    """Exception raised when writing to Modbus register fails."""


class SungrowModbusController:
    """Write-only Modbus TCP controller for Sungrow inverters.

    Never reads any data via Modbus, strictly executes write operations to set
    max charging power on the configured inverters.
    """

    def __init__(
        self,
        inverter_hosts: Sequence[InverterHost],
        register: int = 33047,
        register_offset: int = -1,
        slave_id: int = 1,
        timeout: float = 5.0,
    ) -> None:
        self.inverter_hosts = list(inverter_hosts)
        self.register = register
        self.register_offset = register_offset
        self.slave_id = slave_id
        self.timeout = timeout

    @property
    def effective_register(self) -> int:
        """Effective 0-indexed register address passed over Modbus wire."""
        return self.register + self.register_offset

    def write_max_charging_power(
        self,
        power_kw: float,
        dry_run: bool = False,
    ) -> dict[str, bool]:
        """Write the max charging power in kW to all configured inverters.

        Parameters:
            power_kw: Desired max charging power in kilowatts (e.g. 1.0 or 10.6).
            dry_run: If True, simulate the write without connecting or sending commands.

        Returns:
            Dictionary mapping inverter string 'host:port' to boolean success status.

        Raises:
            ModbusWriteError: If all inverters fail to accept the write command in live mode.
        """
        raw_val = power_kw_to_register_value(power_kw)
        eff_reg = self.effective_register

        logger.info(
            "Setting max charging power: %.2f kW (raw register 0x%04X / %d at address %d, offset %d)",
            power_kw,
            raw_val,
            raw_val,
            eff_reg,
            self.register_offset,
        )

        if dry_run:
            logger.info(
                "[DRY-RUN] Modbus write simulated for %d inverters (address: %d, value: %d / %.2f kW)",
                len(self.inverter_hosts),
                eff_reg,
                raw_val,
                power_kw,
            )
            return {str(inverter): True for inverter in self.inverter_hosts}

        results: dict[str, bool] = {}
        errors: list[str] = []

        for inverter in self.inverter_hosts:
            inv_str = str(inverter)
            try:
                self._write_single_inverter(inverter, eff_reg, raw_val)
                results[inv_str] = True
                logger.info(
                    "Successfully wrote max charging power %.2f kW to inverter %s",
                    power_kw,
                    inv_str,
                )
            except Exception as exc:  # noqa: BLE001
                results[inv_str] = False
                err_msg = f"Failed to write to inverter {inv_str}: {exc}"
                logger.error(err_msg)
                errors.append(err_msg)

        # If at least one inverter succeeded, consider partial success, but if ALL failed, raise
        if all(not success for success in results.values()):
            raise ModbusWriteError(
                f"Modbus write failed on all {len(self.inverter_hosts)} inverters. Errors: {'; '.join(errors)}"
            )

        return results

    def _write_single_inverter(
        self,
        inverter: InverterHost,
        address: int,
        value: int,
    ) -> None:
        """Connect to a single inverter and execute write_register."""
        client = ModbusTcpClient(
            host=inverter.host,
            port=inverter.port,
            timeout=self.timeout,
        )

        try:
            connected = client.connect()
            if not connected:
                raise ModbusConnectionError(
                    f"Could not open TCP connection to {inverter.host}:{inverter.port}"
                )

            write_kwargs: dict[str, Any] = (
                {"device_id": self.slave_id}
                if _write_accepts_device_id
                else {"slave": self.slave_id}
            )

            response = client.write_register(
                address=address,
                value=value,
                **write_kwargs,
            )

            if response is None:
                raise ModbusWriteError(
                    f"No response received from {inverter.host}:{inverter.port} for register write"
                )

            if response.isError():
                raise ModbusWriteError(
                    f"Inverter {inverter.host}:{inverter.port} returned Modbus exception: {response}"
                )

        except (ModbusException, OSError) as exc:
            raise ModbusWriteError(
                f"Modbus communication error with {inverter.host}:{inverter.port}: {exc}"
            ) from exc
        finally:
            client.close()


class ModbusConnectionError(ModbusError):
    """Exception raised when connecting to inverter fails."""
