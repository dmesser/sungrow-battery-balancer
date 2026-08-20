"""End-to-end integration and orchestration tests for BatteryBalancer service."""

import pytest

from sungrow_battery_balancer.balancer import BatteryBalancer
from sungrow_battery_balancer.config import Config, InverterHost
from sungrow_battery_balancer.grafana import GrafanaAnnotationError, GrafanaClient
from sungrow_battery_balancer.influx import InfluxClient, InfluxQueryError
from sungrow_battery_balancer.modbus import ModbusWriteError, SungrowModbusController


@pytest.fixture
def mock_config():
    return Config(
        influxdb_url="https://influx.local:8086",
        influxdb_user="test_user",
        influxdb_password="test_password",
        influxdb_db="pv_monitoring",
        inverter_hosts=[
            InverterHost("192.168.1.8", 502),
            InverterHost("192.168.1.75", 502),
        ],
        grafana_url="https://grafana.local",
        grafana_token="test_token",
        grafana_dashboard_uid="l1PwaTigk",
        grafana_panel_id=2,
        high_soc_threshold=85.0,
        low_soc_threshold=80.0,
        full_soc_threshold=100.0,
        reduced_charging_power=1.0,
        max_charging_power=10.6,
        check_interval=1,
    )


@pytest.fixture
def balancer(mock_config, mocker):
    mock_influx = mocker.create_autospec(InfluxClient, instance=True)
    mock_modbus = mocker.create_autospec(SungrowModbusController, instance=True)
    mock_grafana = mocker.create_autospec(GrafanaClient, instance=True)

    # Defaults
    mock_modbus.write_max_charging_power.return_value = {
        "192.168.1.8:502": True,
        "192.168.1.75:502": True,
    }
    mock_grafana.create_annotation.return_value = 1001

    return BatteryBalancer(
        config=mock_config,
        influx_client=mock_influx,
        modbus_controller=mock_modbus,
        grafana_client=mock_grafana,
    )


class TestBatteryBalancer:
    def test_first_cycle_below_80_soc(self, balancer):
        # Current SoC is 27.3% (initial state) -> Sets 10.6 kW
        balancer.influx_client.fetch_battery_soc.return_value = 27.3

        result = balancer.run_once()

        assert result.soc == 27.3
        assert result.decision.target_power_kw == 10.6
        assert result.modbus_written is True
        assert result.grafana_annotated is True
        assert balancer.current_power_kw == 10.6
        balancer.modbus_controller.write_max_charging_power.assert_called_once_with(
            power_kw=10.6, dry_run=False
        )
        balancer.grafana_client.create_annotation.assert_called_once()

    def test_subsequent_cycle_unchanged_soc_skips_writes(self, balancer):
        balancer.influx_client.fetch_battery_soc.return_value = 50.0
        balancer.run_once()

        balancer.modbus_controller.write_max_charging_power.reset_mock()
        balancer.grafana_client.create_annotation.reset_mock()

        # Second cycle: SoC is 55.0% -> still < 80.0%, power remains 10.6 kW
        balancer.influx_client.fetch_battery_soc.return_value = 55.0
        result2 = balancer.run_once()

        assert result2.decision.state_changed is False
        assert result2.modbus_written is False
        assert result2.grafana_annotated is False
        balancer.modbus_controller.write_max_charging_power.assert_not_called()
        balancer.grafana_client.create_annotation.assert_not_called()

    def test_soc_rises_above_85_triggers_power_reduction(self, balancer):
        balancer.current_power_kw = 10.6
        balancer.influx_client.fetch_battery_soc.return_value = 85.5

        result = balancer.run_once()

        assert result.decision.target_power_kw == 1.0
        assert result.decision.state_changed is True
        assert result.modbus_written is True
        assert balancer.current_power_kw == 1.0
        balancer.modbus_controller.write_max_charging_power.assert_called_once_with(
            power_kw=1.0, dry_run=False
        )

    def test_manual_set_power_override(self, balancer):
        balancer.influx_client.fetch_battery_soc.return_value = 85.0

        result = balancer.run_once(force_power_kw=7.5)

        assert result.decision.target_power_kw == 7.5
        assert result.decision.register_raw_value == 750
        assert result.modbus_written is True
        assert balancer.current_power_kw == 7.5
        balancer.modbus_controller.write_max_charging_power.assert_called_once_with(
            power_kw=7.5, dry_run=False
        )

    def test_grafana_failure_is_non_fatal(self, balancer):
        balancer.influx_client.fetch_battery_soc.return_value = 86.0
        balancer.grafana_client.create_annotation.side_effect = GrafanaAnnotationError("Timeout")

        # Modbus succeeds, Grafana fails -> run_once completes and updates state
        result = balancer.run_once()

        assert result.modbus_written is True
        assert result.grafana_annotated is False
        assert balancer.current_power_kw == 1.0

    def test_modbus_failure_raises_and_does_not_update_state(self, balancer):
        balancer.influx_client.fetch_battery_soc.return_value = 86.0
        balancer.modbus_controller.write_max_charging_power.side_effect = ModbusWriteError("Failed")

        with pytest.raises(ModbusWriteError):
            balancer.run_once()

        # State should NOT be updated to 1.0 kW because write failed
        assert balancer.current_power_kw is None

    def test_one_shot_execution(self, balancer, capsys):
        balancer.config.one_shot = True
        balancer.influx_client.fetch_battery_soc.return_value = 88.0

        result = balancer.run_one_shot()

        assert result.soc == 88.0
        assert result.decision.target_power_kw == 1.0
        captured = capsys.readouterr()
        assert "ONE-SHOT EXECUTION REPORT" in captured.out
        assert "Target Charging Power    : 1.00 kW" in captured.out

    def test_loop_runs_and_handles_errors_gracefully(self, balancer, mocker):
        # First iteration: Influx throws error
        # Second iteration: Success
        # Then stop
        balancer.influx_client.fetch_battery_soc.side_effect = [
            InfluxQueryError("Connection reset"),
            27.3,
        ]

        def stop_after_second():
            if balancer.influx_client.fetch_battery_soc.call_count >= 2:
                balancer.stop()

        # Patch run_once or sleep to trigger stop
        original_run_once = balancer.run_once

        def wrapped_run_once(*args, **kwargs):
            try:
                res = original_run_once(*args, **kwargs)
            finally:
                stop_after_second()
            return res

        balancer.run_once = wrapped_run_once

        balancer.run_loop()

        assert balancer.influx_client.fetch_battery_soc.call_count == 2
        assert balancer.current_power_kw == 10.6

    def test_loop_handles_modbus_and_unexpected_errors(self, balancer, mocker):
        balancer.influx_client.fetch_battery_soc.side_effect = [
            27.3,
            27.3,
            27.3,
        ]
        # First iteration: Modbus error
        # Second iteration: Unexpected generic exception
        # Third iteration: Clean success
        balancer.modbus_controller.write_max_charging_power.side_effect = [
            ModbusWriteError("Connection refused"),
            RuntimeError("Unexpected glitch"),
            {"192.168.1.8:502": True, "192.168.1.75:502": True},
        ]

        def stop_after_third():
            if balancer.influx_client.fetch_battery_soc.call_count >= 3:
                balancer.stop()

        original_run_once = balancer.run_once

        def wrapped_run_once(*args, **kwargs):
            try:
                res = original_run_once(*args, **kwargs)
            finally:
                stop_after_third()
            return res

        balancer.run_once = wrapped_run_once
        balancer.run_loop()

        assert balancer.influx_client.fetch_battery_soc.call_count == 3
        assert balancer.current_power_kw == 10.6

    def test_close_balancer(self, balancer):
        balancer.close()
        balancer.influx_client.close.assert_called_once()
        balancer.grafana_client.close.assert_called_once()
