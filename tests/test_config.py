"""Tests for configuration loading, CLI flag overrides, validation, and error reporting."""

import pytest

from sungrow_battery_balancer.config import (
    InverterHost,
    load_config,
    load_dotenv_file,
    mask_secret,
    parse_inverter_hosts,
    print_config_summary,
    str_to_bool,
)


@pytest.fixture
def complete_env():
    """Valid environment dictionary with all required fields."""
    return {
        "INFLUXDB_URL": "https://influxdb.example.com:8086",
        "INFLUXDB_USER": "pv-monitoring-read",
        "INFLUXDB_PASSWORD": "secret_influx_password",
        "INFLUXDB_DB": "pv_monitoring",
        "INVERTER_HOSTS": "192.168.1.8:502,192.168.1.75:502",
        "GRAFANA_URL": "https://grafana.example.com",
        "GRAFANA_TOKEN": "test_grafana_secret_token",
        "GRAFANA_DASHBOARD_UID": "l1PwaTigk",
        "GRAFANA_PANEL_ID": "2",
        "REDUCED_CHARGING_POWER": "1.0",
        "MAX_CHARGING_POWER": "10.6",
    }


class TestHelpers:
    def test_parse_inverter_hosts(self):
        assert parse_inverter_hosts("") == []
        assert parse_inverter_hosts("   ") == []

        hosts = parse_inverter_hosts("192.168.1.8:502, 192.168.1.75:502")
        assert len(hosts) == 2
        assert hosts[0] == InverterHost("192.168.1.8", 502)
        assert hosts[1] == InverterHost("192.168.1.75", 502)

        # Default port 502 when omitted
        hosts_no_port = parse_inverter_hosts("10.0.0.1, 10.0.0.2:5020")
        assert hosts_no_port[0] == InverterHost("10.0.0.1", 502)
        assert hosts_no_port[1] == InverterHost("10.0.0.2", 5020)

    def test_str_to_bool(self):
        assert str_to_bool("true") is True
        assert str_to_bool("1") is True
        assert str_to_bool("yes") is True
        assert str_to_bool("false") is False
        assert str_to_bool("0") is False
        assert str_to_bool("no") is False
        assert str_to_bool(None, default=True) is True
        assert str_to_bool(None, default=False) is False

    def test_mask_secret(self):
        assert mask_secret(None) == "<not set>"
        assert mask_secret("") == "<not set>"
        assert mask_secret("abc") == "****"
        assert mask_secret("abcdefgh") == "ab****gh"

    def test_load_dotenv_file(self, tmp_path):
        env_file = tmp_path / "test.env"
        env_file.write_text(
            "# Comment line\n"
            "KEY1=value1\n"
            "KEY2=\"quoted_value\"\n"
            "KEY3='single_quoted'\n"
            "INVALID_LINE\n"
            "KEY4=val with spaces\n"
        )
        parsed = load_dotenv_file(str(env_file))
        assert parsed["KEY1"] == "value1"
        assert parsed["KEY2"] == "quoted_value"
        assert parsed["KEY3"] == "single_quoted"
        assert parsed["KEY4"] == "val with spaces"
        assert "INVALID_LINE" not in parsed

        # Non-existent file
        assert load_dotenv_file(str(tmp_path / "non_existent.env")) == {}



class TestLoadConfig:
    def test_load_from_env(self, complete_env):
        config = load_config(args=[], environ=complete_env)
        assert config.influxdb_url == "https://influxdb.example.com:8086"
        assert config.influxdb_user == "pv-monitoring-read"
        assert config.influxdb_password == "secret_influx_password"
        assert config.influxdb_db == "pv_monitoring"
        assert len(config.inverter_hosts) == 2
        assert config.effective_modbus_register == 33046
        assert config.grafana_url == "https://grafana.example.com"
        assert config.grafana_token == "test_grafana_secret_token"
        assert config.grafana_dashboard_uid == "l1PwaTigk"
        assert config.grafana_panel_id == 2
        assert config.reduced_charging_power == 1.0
        assert config.max_charging_power == 10.6
        assert config.check_interval == 60
        assert config.dry_run is False
        assert config.one_shot is False

    def test_cli_overrides_env(self, complete_env):
        cli_args = [
            "--influxdb-url",
            "https://override-influx.local:8086",
            "--inverter-hosts",
            "10.10.10.10:502",
            "--grafana-panel-id",
            "99",
            "--reduced-charging-power",
            "2.5",
            "--max-charging-power",
            "11.0",
            "--high-soc-threshold",
            "90.0",
            "--low-soc-threshold",
            "75.0",
            "--check-interval",
            "30",
            "--no-verify-ssl",
            "--dry-run",
            "--one-shot",
            "--set-power",
            "4.2",
            "--log-level",
            "DEBUG",
        ]
        config = load_config(args=cli_args, environ=complete_env)
        assert config.influxdb_url == "https://override-influx.local:8086"
        assert len(config.inverter_hosts) == 1
        assert config.inverter_hosts[0].host == "10.10.10.10"
        assert config.grafana_panel_id == 99
        assert config.reduced_charging_power == 2.5
        assert config.max_charging_power == 11.0
        assert config.high_soc_threshold == 90.0
        assert config.low_soc_threshold == 75.0
        assert config.check_interval == 30
        assert config.influxdb_verify_ssl is False
        assert config.dry_run is True
        assert config.one_shot is True
        assert config.set_power == 4.2
        assert config.log_level == "DEBUG"

    def test_missing_required_params_exits(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            load_config(args=[], environ={})
        assert exc_info.value.code == 1

    def test_invalid_panel_id_exits(self, complete_env, capsys):
        complete_env["GRAFANA_PANEL_ID"] = "not_an_int"
        with pytest.raises(SystemExit) as exc_info:
            load_config(args=[], environ=complete_env)
        assert exc_info.value.code == 1

    def test_summary_format(self, complete_env):
        config = load_config(args=["--set-power", "3.5"], environ=complete_env)
        summary = print_config_summary(config)
        assert "Sungrow Battery Balancer Configuration" in summary
        assert "pv-monitoring-read" in summary
        assert "se****rd" in summary  # masked password
        assert "3.5 kW" in summary
