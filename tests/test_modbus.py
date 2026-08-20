"""Tests for Modbus TCP controller, multi-inverter writes, and error handling."""

import pytest

from sungrow_battery_balancer.config import InverterHost
from sungrow_battery_balancer.modbus import (
    ModbusWriteError,
    SungrowModbusController,
)


@pytest.fixture
def dual_inverters():
    return [
        InverterHost(host="192.168.1.8", port=502),
        InverterHost(host="192.168.1.75", port=502),
    ]


@pytest.fixture
def modbus_controller(dual_inverters):
    return SungrowModbusController(
        inverter_hosts=dual_inverters,
        register=33047,
        register_offset=-1,
        slave_id=1,
        timeout=5.0,
    )


class TestModbusController:
    def test_effective_register_calculation(self, modbus_controller):
        # 33047 - 1 = 33046
        assert modbus_controller.effective_register == 33046

    def test_dry_run_does_not_open_sockets(self, modbus_controller, mocker):
        mock_client_cls = mocker.patch("sungrow_battery_balancer.modbus.ModbusTcpClient")
        results = modbus_controller.write_max_charging_power(power_kw=1.0, dry_run=True)

        assert results == {
            "192.168.1.8:502": True,
            "192.168.1.75:502": True,
        }
        mock_client_cls.assert_not_called()

    def test_successful_write_both_inverters(self, modbus_controller, mocker):
        mock_client = mocker.MagicMock()
        mock_client.connect.return_value = True
        mock_response = mocker.MagicMock()
        mock_response.isError.return_value = False
        mock_client.write_register.return_value = mock_response

        mocker.patch("sungrow_battery_balancer.modbus.ModbusTcpClient", return_value=mock_client)

        results = modbus_controller.write_max_charging_power(power_kw=10.6, dry_run=False)

        assert results == {
            "192.168.1.8:502": True,
            "192.168.1.75:502": True,
        }
        assert mock_client.connect.call_count == 2
        assert mock_client.write_register.call_count == 2
        # Address 33046, value 1060 (10.6 kW * 100)
        mock_client.write_register.assert_called_with(
            address=33046,
            value=1060,
            device_id=1,
        )
        assert mock_client.close.call_count == 2

    def test_connection_failure_all_inverters(self, modbus_controller, mocker):
        mock_client = mocker.MagicMock()
        mock_client.connect.return_value = False
        mocker.patch("sungrow_battery_balancer.modbus.ModbusTcpClient", return_value=mock_client)

        with pytest.raises(ModbusWriteError, match="failed on all 2 inverters"):
            modbus_controller.write_max_charging_power(power_kw=1.0, dry_run=False)

    def test_modbus_exception_response(self, modbus_controller, mocker):
        mock_client = mocker.MagicMock()
        mock_client.connect.return_value = True
        mock_response = mocker.MagicMock()
        mock_response.isError.return_value = True
        mock_client.write_register.return_value = mock_response

        mocker.patch("sungrow_battery_balancer.modbus.ModbusTcpClient", return_value=mock_client)

        with pytest.raises(ModbusWriteError, match="returned Modbus exception"):
            modbus_controller.write_max_charging_power(power_kw=1.0, dry_run=False)

    def test_modbus_none_response(self, modbus_controller, mocker):
        mock_client = mocker.MagicMock()
        mock_client.connect.return_value = True
        mock_client.write_register.return_value = None
        mocker.patch("sungrow_battery_balancer.modbus.ModbusTcpClient", return_value=mock_client)

        with pytest.raises(ModbusWriteError, match="No response received"):
            modbus_controller.write_max_charging_power(power_kw=1.0, dry_run=False)

    def test_modbus_os_error(self, modbus_controller, mocker):
        mock_client = mocker.MagicMock()
        mock_client.connect.return_value = True
        mock_client.write_register.side_effect = OSError("Socket broken")
        mocker.patch("sungrow_battery_balancer.modbus.ModbusTcpClient", return_value=mock_client)

        with pytest.raises(ModbusWriteError, match="communication error"):
            modbus_controller.write_max_charging_power(power_kw=1.0, dry_run=False)

    def test_partial_failure_does_not_raise_if_one_succeeds(self, modbus_controller, mocker):
        # Inverter 1 succeeds, Inverter 2 fails
        mock_client1 = mocker.MagicMock()
        mock_client1.connect.return_value = True
        resp1 = mocker.MagicMock()
        resp1.isError.return_value = False
        mock_client1.write_register.return_value = resp1

        mock_client2 = mocker.MagicMock()
        mock_client2.connect.return_value = False

        mocker.patch(
            "sungrow_battery_balancer.modbus.ModbusTcpClient",
            side_effect=[mock_client1, mock_client2],
        )

        results = modbus_controller.write_max_charging_power(power_kw=1.0, dry_run=False)
        assert results["192.168.1.8:502"] is True
        assert results["192.168.1.75:502"] is False
