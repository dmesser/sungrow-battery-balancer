"""Configuration management for Sungrow Battery Balancer.

Loads parameters from CLI arguments and environment variables, enforcing
CLI precedence and validating required settings with clear human-friendly error messages.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

DEFAULT_INFLUXDB_QUERY = (
    "SELECT MAX(last_battery_level) "
    "FROM ("
    "SELECT last(battery_level) as last_battery_level "
    "FROM sungather "
    "GROUP BY serial fill(previous)"
    ")"
)


@dataclass
class InverterHost:
    host: str
    port: int = 502

    def __str__(self) -> str:
        return f"{self.host}:{self.port}"


@dataclass
class Config:
    # InfluxDB Configuration
    influxdb_url: str
    influxdb_user: str
    influxdb_password: str
    influxdb_db: str
    influxdb_query: str = DEFAULT_INFLUXDB_QUERY
    influxdb_verify_ssl: bool = True

    # Sungrow Inverter Configuration
    inverter_hosts: list[InverterHost] = field(default_factory=list)
    modbus_slave_id: int = 1
    modbus_register: int = 33047
    modbus_register_offset: int = -1
    modbus_timeout: float = 5.0

    # Grafana Configuration
    grafana_url: str = ""
    grafana_token: str = ""
    grafana_dashboard_uid: str = ""
    grafana_panel_id: int = 0

    # SoC & Power Thresholds
    high_soc_threshold: float = 85.0
    low_soc_threshold: float = 80.0
    full_soc_threshold: float = 100.0
    reduced_charging_power: float = 1.0  # kW
    max_charging_power: float = 10.6  # kW

    # Operational Options
    check_interval: int = 60  # seconds
    log_level: str = "INFO"
    dry_run: bool = False
    one_shot: bool = False
    set_power: float | None = None  # Explicit target power in kW for one-shot mode

    @property
    def effective_modbus_register(self) -> int:
        """Calculate 0-indexed Modbus register according to Sungrow protocol spec."""
        return self.modbus_register + self.modbus_register_offset


def parse_inverter_hosts(hosts_str: str) -> list[InverterHost]:
    """Parse comma-separated inverter host string 'host1:port1,host2:port2'."""
    results: list[InverterHost] = []
    if not hosts_str or not hosts_str.strip():
        return results

    for item in hosts_str.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            parts = item.split(":", 1)
            host = parts[0].strip()
            port = int(parts[1].strip())
            results.append(InverterHost(host=host, port=port))
        else:
            results.append(InverterHost(host=item, port=502))
    return results


def str_to_bool(val: str | None, default: bool = True) -> bool:
    """Convert string to boolean."""
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on", "t"}


def mask_secret(secret: str | None) -> str:
    """Safely mask sensitive strings for display."""
    if not secret:
        return "<not set>"
    if len(secret) <= 4:
        return "****"
    return f"{secret[:2]}****{secret[-2:]}"


def build_parser() -> argparse.ArgumentParser:
    """Build CLI ArgumentParser with full configuration switches."""
    parser = argparse.ArgumentParser(
        prog="sungrow-battery-balancer",
        description=(
            "Dynamic maximum charging power balancer for Sungrow SBR256 battery "
            "and dual Sungrow SH10RT-20 inverters using InfluxDB metrics and Grafana annotations."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Operational Modes
    mode_group = parser.add_argument_group("Execution Modes")
    mode_group.add_argument(
        "-1",
        "--one-shot",
        action="store_true",
        dest="one_shot",
        help="Run once, report status/action, and exit immediately.",
    )
    mode_group.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Simulate execution without writing to Modbus or creating Grafana annotations.",
    )
    mode_group.add_argument(
        "--set-power",
        type=float,
        dest="set_power",
        metavar="KW",
        help="Explicitly set charging power (kW) in one-shot mode, bypassing automatic SoC logic.",
    )
    mode_group.add_argument(
        "-i",
        "--check-interval",
        type=int,
        dest="check_interval",
        metavar="SEC",
        help="Monitoring loop interval in seconds (env: CHECK_INTERVAL).",
    )
    mode_group.add_argument(
        "--log-level",
        type=str,
        dest="log_level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level (env: LOG_LEVEL).",
    )

    # InfluxDB Options
    influx_group = parser.add_argument_group("InfluxDB 1.8 Configuration")
    influx_group.add_argument(
        "--influxdb-url",
        type=str,
        dest="influxdb_url",
        metavar="URL",
        help="InfluxDB base URL (env: INFLUXDB_URL).",
    )
    influx_group.add_argument(
        "--influxdb-user",
        type=str,
        dest="influxdb_user",
        metavar="USER",
        help="InfluxDB username (env: INFLUXDB_USER).",
    )
    influx_group.add_argument(
        "--influxdb-password",
        type=str,
        dest="influxdb_password",
        metavar="PASS",
        help="InfluxDB password (env: INFLUXDB_PASSWORD).",
    )
    influx_group.add_argument(
        "--influxdb-db",
        type=str,
        dest="influxdb_db",
        metavar="DB",
        help="InfluxDB database name (env: INFLUXDB_DB).",
    )
    influx_group.add_argument(
        "--influxdb-query",
        type=str,
        dest="influxdb_query",
        metavar="QUERY",
        help="InfluxQL query string to retrieve SoC (env: INFLUXDB_QUERY).",
    )
    influx_group.add_argument(
        "--no-verify-ssl",
        action="store_false",
        dest="influxdb_verify_ssl",
        default=None,
        help="Disable SSL certificate verification for InfluxDB (env: INFLUXDB_VERIFY_SSL).",
    )

    # Modbus / Inverter Options
    modbus_group = parser.add_argument_group("Sungrow Inverter Modbus Configuration")
    modbus_group.add_argument(
        "--inverter-hosts",
        type=str,
        dest="inverter_hosts",
        metavar="HOSTS",
        help="Comma-separated list of inverter IP[:PORT] (env: INVERTER_HOSTS).",
    )
    modbus_group.add_argument(
        "--modbus-slave-id",
        type=int,
        dest="modbus_slave_id",
        metavar="ID",
        help="Modbus slave/unit ID (env: MODBUS_SLAVE_ID).",
    )
    modbus_group.add_argument(
        "--modbus-register",
        type=int,
        dest="modbus_register",
        metavar="REG",
        help="Max Charging Power register address (env: MODBUS_REGISTER).",
    )
    modbus_group.add_argument(
        "--modbus-register-offset",
        type=int,
        dest="modbus_register_offset",
        metavar="OFFSET",
        help="Offset applied to register address (env: MODBUS_REGISTER_OFFSET).",
    )
    modbus_group.add_argument(
        "--modbus-timeout",
        type=float,
        dest="modbus_timeout",
        metavar="SEC",
        help="Modbus TCP connection and response timeout (env: MODBUS_TIMEOUT).",
    )

    # Grafana Options
    grafana_group = parser.add_argument_group("Grafana Configuration")
    grafana_group.add_argument(
        "--grafana-url",
        type=str,
        dest="grafana_url",
        metavar="URL",
        help="Grafana base URL (env: GRAFANA_URL).",
    )
    grafana_group.add_argument(
        "--grafana-token",
        type=str,
        dest="grafana_token",
        metavar="TOKEN",
        help="Grafana Service Account or API token (env: GRAFANA_TOKEN).",
    )
    grafana_group.add_argument(
        "--grafana-dashboard-uid",
        type=str,
        dest="grafana_dashboard_uid",
        metavar="UID",
        help="Grafana dashboard UID (env: GRAFANA_DASHBOARD_UID).",
    )
    grafana_group.add_argument(
        "--grafana-panel-id",
        type=int,
        dest="grafana_panel_id",
        metavar="ID",
        help="Grafana panel ID for annotations (env: GRAFANA_PANEL_ID).",
    )

    # SoC & Power Thresholds
    threshold_group = parser.add_argument_group("Thresholds & Power Settings")
    threshold_group.add_argument(
        "--high-soc-threshold",
        type=float,
        dest="high_soc_threshold",
        metavar="PCT",
        help="SoC percent threshold to throttle charge power (env: HIGH_SOC_THRESHOLD).",
    )
    threshold_group.add_argument(
        "--low-soc-threshold",
        type=float,
        dest="low_soc_threshold",
        metavar="PCT",
        help="SoC percent threshold to restore max charge power (env: LOW_SOC_THRESHOLD).",
    )
    threshold_group.add_argument(
        "--full-soc-threshold",
        type=float,
        dest="full_soc_threshold",
        metavar="PCT",
        help="SoC percent threshold considered 100%% (env: FULL_SOC_THRESHOLD).",
    )
    threshold_group.add_argument(
        "--reduced-charging-power",
        type=float,
        dest="reduced_charging_power",
        metavar="KW",
        help="Reduced charging power in kW when SoC >= high threshold (env: REDUCED_CHARGING_POWER).",
    )
    threshold_group.add_argument(
        "--max-charging-power",
        type=float,
        dest="max_charging_power",
        metavar="KW",
        help="Max charging power in kW when SoC < low or >= full (env: MAX_CHARGING_POWER).",
    )

    return parser


def load_config(
    args: Sequence[str] | None = None,
    environ: dict[str, str] | None = None,
) -> Config:
    """Load configuration combining CLI args and environment variables.

    CLI arguments take precedence over environment variables.
    Validates required settings and raises SystemExit with human-friendly message if missing.
    """
    if environ is None:
        environ = os.environ  # pragma: no cover

    parser = build_parser()
    parsed_args = parser.parse_args(args)

    # Helper to resolve value: CLI > Env > Default
    def get_val(cli_val, env_key: str, default=None):
        if cli_val is not None:
            return cli_val
        if env_key in environ and environ[env_key] != "":
            return environ[env_key]
        return default

    # Track missing mandatory configuration
    missing_items: list[tuple[str, str, str]] = []

    # InfluxDB
    influxdb_url = get_val(parsed_args.influxdb_url, "INFLUXDB_URL")
    if not influxdb_url:
        missing_items.append(("InfluxDB URL", "--influxdb-url", "INFLUXDB_URL"))

    influxdb_user = get_val(parsed_args.influxdb_user, "INFLUXDB_USER")
    if not influxdb_user:
        missing_items.append(("InfluxDB Username", "--influxdb-user", "INFLUXDB_USER"))

    influxdb_password = get_val(parsed_args.influxdb_password, "INFLUXDB_PASSWORD")
    if not influxdb_password:
        missing_items.append(("InfluxDB Password", "--influxdb-password", "INFLUXDB_PASSWORD"))

    influxdb_db = get_val(parsed_args.influxdb_db, "INFLUXDB_DB")
    if not influxdb_db:
        missing_items.append(("InfluxDB Database", "--influxdb-db", "INFLUXDB_DB"))

    influxdb_query = get_val(
        parsed_args.influxdb_query, "INFLUXDB_QUERY", default=DEFAULT_INFLUXDB_QUERY
    )

    # SSL verification: CLI flag (False if --no-verify-ssl) > Env > Default True
    if parsed_args.influxdb_verify_ssl is not None:
        influxdb_verify_ssl = parsed_args.influxdb_verify_ssl
    else:
        influxdb_verify_ssl = str_to_bool(environ.get("INFLUXDB_VERIFY_SSL"), default=True)

    # Inverter Hosts
    inverter_hosts_str = get_val(parsed_args.inverter_hosts, "INVERTER_HOSTS")
    if not inverter_hosts_str:
        missing_items.append(("Inverter Hosts (IP:Port)", "--inverter-hosts", "INVERTER_HOSTS"))
        inverter_hosts = []
    else:
        inverter_hosts = parse_inverter_hosts(inverter_hosts_str)
        if not inverter_hosts:
            missing_items.append(
                ("Inverter Hosts (Invalid format)", "--inverter-hosts", "INVERTER_HOSTS")
            )

    modbus_slave_id = int(get_val(parsed_args.modbus_slave_id, "MODBUS_SLAVE_ID", default=1))
    modbus_register = int(get_val(parsed_args.modbus_register, "MODBUS_REGISTER", default=33047))
    modbus_register_offset = int(
        get_val(parsed_args.modbus_register_offset, "MODBUS_REGISTER_OFFSET", default=-1)
    )
    modbus_timeout = float(get_val(parsed_args.modbus_timeout, "MODBUS_TIMEOUT", default=5.0))

    # Grafana
    grafana_url = get_val(parsed_args.grafana_url, "GRAFANA_URL")
    if not grafana_url:
        missing_items.append(("Grafana URL", "--grafana-url", "GRAFANA_URL"))

    grafana_token = get_val(parsed_args.grafana_token, "GRAFANA_TOKEN")
    if not grafana_token:
        missing_items.append(("Grafana Token", "--grafana-token", "GRAFANA_TOKEN"))

    grafana_dashboard_uid = get_val(parsed_args.grafana_dashboard_uid, "GRAFANA_DASHBOARD_UID")
    if not grafana_dashboard_uid:
        missing_items.append(
            ("Grafana Dashboard UID", "--grafana-dashboard-uid", "GRAFANA_DASHBOARD_UID")
        )

    grafana_panel_id_raw = get_val(parsed_args.grafana_panel_id, "GRAFANA_PANEL_ID")
    if grafana_panel_id_raw is None or str(grafana_panel_id_raw).strip() == "":
        missing_items.append(("Grafana Panel ID", "--grafana-panel-id", "GRAFANA_PANEL_ID"))
        grafana_panel_id = 0
    else:
        try:
            grafana_panel_id = int(grafana_panel_id_raw)
        except ValueError:
            missing_items.append(
                ("Grafana Panel ID (must be integer)", "--grafana-panel-id", "GRAFANA_PANEL_ID")
            )
            grafana_panel_id = 0

    # Power & Thresholds
    high_soc_threshold = float(
        get_val(parsed_args.high_soc_threshold, "HIGH_SOC_THRESHOLD", default=85.0)
    )
    low_soc_threshold = float(
        get_val(parsed_args.low_soc_threshold, "LOW_SOC_THRESHOLD", default=80.0)
    )
    full_soc_threshold = float(
        get_val(parsed_args.full_soc_threshold, "FULL_SOC_THRESHOLD", default=100.0)
    )

    reduced_charging_power_raw = get_val(
        parsed_args.reduced_charging_power, "REDUCED_CHARGING_POWER"
    )
    if reduced_charging_power_raw is None:
        missing_items.append(
            ("Reduced Charging Power", "--reduced-charging-power", "REDUCED_CHARGING_POWER")
        )
        reduced_charging_power = 1.0
    else:
        reduced_charging_power = float(reduced_charging_power_raw)

    max_charging_power_raw = get_val(parsed_args.max_charging_power, "MAX_CHARGING_POWER")
    if max_charging_power_raw is None:
        missing_items.append(("Max Charging Power", "--max-charging-power", "MAX_CHARGING_POWER"))
        max_charging_power = 10.6
    else:
        max_charging_power = float(max_charging_power_raw)

    # Operational Options
    check_interval = int(get_val(parsed_args.check_interval, "CHECK_INTERVAL", default=60))
    log_level = str(get_val(parsed_args.log_level, "LOG_LEVEL", default="INFO")).upper()
    dry_run = bool(parsed_args.dry_run or str_to_bool(environ.get("DRY_RUN"), default=False))
    one_shot = bool(parsed_args.one_shot or str_to_bool(environ.get("ONE_SHOT"), default=False))
    set_power = parsed_args.set_power

    if missing_items:
        _print_missing_config_error(missing_items)
        sys.exit(1)

    return Config(
        influxdb_url=str(influxdb_url),
        influxdb_user=str(influxdb_user),
        influxdb_password=str(influxdb_password),
        influxdb_db=str(influxdb_db),
        influxdb_query=str(influxdb_query),
        influxdb_verify_ssl=influxdb_verify_ssl,
        inverter_hosts=inverter_hosts,
        modbus_slave_id=modbus_slave_id,
        modbus_register=modbus_register,
        modbus_register_offset=modbus_register_offset,
        modbus_timeout=modbus_timeout,
        grafana_url=str(grafana_url),
        grafana_token=str(grafana_token),
        grafana_dashboard_uid=str(grafana_dashboard_uid),
        grafana_panel_id=grafana_panel_id,
        high_soc_threshold=high_soc_threshold,
        low_soc_threshold=low_soc_threshold,
        full_soc_threshold=full_soc_threshold,
        reduced_charging_power=reduced_charging_power,
        max_charging_power=max_charging_power,
        check_interval=check_interval,
        log_level=log_level,
        dry_run=dry_run,
        one_shot=one_shot,
        set_power=set_power,
    )


def _print_missing_config_error(missing_items: list[tuple[str, str, str]]) -> None:
    """Render a human-friendly error message for missing required configuration."""
    err = sys.stderr
    err.write("\n" + "=" * 78 + "\n")
    err.write(" CONFIGURATION ERROR: Missing Required Parameters\n")
    err.write("=" * 78 + "\n\n")
    err.write(
        "The following required configuration parameters were not provided via CLI switches\n"
        "or environment variables:\n\n"
    )
    err.write(f"  {'Parameter':<32} {'CLI Switch':<24} {'Environment Variable':<22}\n")
    err.write(f"  {'-' * 30:<32} {'-' * 22:<24} {'-' * 20:<22}\n")
    for name, cli_flag, env_var in missing_items:
        err.write(f"  {name:<32} {cli_flag:<24} {env_var:<22}\n")

    err.write("\nHow to fix:\n")
    err.write("  1. Create a `.env` file or export the required environment variables.\n")
    err.write(
        "  2. Or pass them directly using CLI flags (e.g. `sungrow-battery-balancer --help`).\n"
    )
    err.write("=" * 78 + "\n\n")


def print_config_summary(config: Config) -> str:
    """Generate a formatted configuration summary with masked secrets for reporting."""
    lines = [
        "----------------------------------------------------------------------",
        " Sungrow Battery Balancer Configuration",
        "----------------------------------------------------------------------",
        f" Execution Mode           : {'ONE-SHOT' if config.one_shot else 'CONTINUOUS LOOP'}",
        f" Dry Run (Simulation)     : {'ENABLED (No writes)' if config.dry_run else 'DISABLED (Active)'}",
        f" Check Interval           : {config.check_interval}s",
        f" Log Level                : {config.log_level}",
        "",
        " InfluxDB 1.8 Configuration:",
        f"   URL                    : {config.influxdb_url}",
        f"   User                   : {config.influxdb_user}",
        f"   Password               : {mask_secret(config.influxdb_password)}",
        f"   Database               : {config.influxdb_db}",
        f"   Verify SSL             : {config.influxdb_verify_ssl}",
        f"   Query                  : {config.influxdb_query}",
        "",
        " Sungrow Inverter Modbus TCP:",
        f"   Target Inverters       : {', '.join(str(h) for h in config.inverter_hosts)}",
        f"   Slave ID               : {config.modbus_slave_id}",
        f"   Modbus Register (Doc)  : {config.modbus_register} (Offset: {config.modbus_register_offset} -> 0-indexed: {config.effective_modbus_register})",
        f"   Modbus Timeout         : {config.modbus_timeout}s",
        "",
        " Grafana Annotations:",
        f"   URL                    : {config.grafana_url}",
        f"   Token                  : {mask_secret(config.grafana_token)}",
        f"   Dashboard UID          : {config.grafana_dashboard_uid}",
        f"   Panel ID               : {config.grafana_panel_id}",
        "",
        " Charging Power & Thresholds:",
        f"   High SoC Threshold     : >= {config.high_soc_threshold:.1f}% -> Throttled Power: {config.reduced_charging_power:.1f} kW",
        f"   Low SoC Threshold      : < {config.low_soc_threshold:.1f}% -> Restored Power: {config.max_charging_power:.1f} kW",
        f"   Full SoC Threshold     : >= {config.full_soc_threshold:.1f}% -> Restored Power: {config.max_charging_power:.1f} kW",
        f"   Hysteresis Deadband    : [{config.low_soc_threshold:.1f}%, {config.high_soc_threshold:.1f}%) maintains current state",
    ]
    if config.set_power is not None:
        lines.append(f"   Explicit Target Power  : {config.set_power:.1f} kW (CLI override)")
    lines.append("----------------------------------------------------------------------")
    return "\n".join(lines)
