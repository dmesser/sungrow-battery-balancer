"""Tests for CLI main entry point and logging setup."""

import pytest

from sungrow_battery_balancer.cli import main, setup_logging


@pytest.fixture
def valid_cli_args():
    return [
        "--influxdb-url",
        "https://influx.example.com",
        "--influxdb-user",
        "read_user",
        "--influxdb-password",
        "secret123",
        "--influxdb-db",
        "pv_db",
        "--inverter-hosts",
        "192.168.1.8:502,192.168.1.75:502",
        "--grafana-url",
        "https://grafana.example.com",
        "--grafana-token",
        "test_grafana_token",
        "--grafana-dashboard-uid",
        "l1PwaTigk",
        "--grafana-panel-id",
        "2",
        "--reduced-charging-power",
        "1.0",
        "--max-charging-power",
        "10.6",
    ]


class TestCLI:
    def test_setup_logging(self):
        setup_logging("DEBUG")
        setup_logging("INVALID_LEVEL")  # Fallback to INFO

    def test_main_one_shot_mode(self, valid_cli_args, mocker):
        mock_balancer = mocker.patch("sungrow_battery_balancer.cli.BatteryBalancer")
        instance = mock_balancer.return_value

        args = valid_cli_args + ["--one-shot", "--dry-run"]
        exit_code = main(args)

        assert exit_code == 0
        instance.run_one_shot.assert_called_once()
        instance.close.assert_called_once()

    def test_main_loop_mode(self, valid_cli_args, mocker):
        mock_balancer = mocker.patch("sungrow_battery_balancer.cli.BatteryBalancer")
        instance = mock_balancer.return_value

        exit_code = main(valid_cli_args)

        assert exit_code == 0
        instance.run_loop.assert_called_once()
        instance.close.assert_called_once()

    def test_main_fatal_exception(self, valid_cli_args, mocker):
        mock_balancer = mocker.patch("sungrow_battery_balancer.cli.BatteryBalancer")
        instance = mock_balancer.return_value
        instance.run_loop.side_effect = RuntimeError("Fatal hardware failure")

        exit_code = main(valid_cli_args)

        assert exit_code == 1
        instance.close.assert_called_once()
