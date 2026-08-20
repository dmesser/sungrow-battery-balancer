"""Core Battery Balancer service coordinating InfluxDB, Modbus, and Grafana."""

from __future__ import annotations

import logging
import signal
import threading
import time
from dataclasses import dataclass

from .config import Config, print_config_summary
from .grafana import GrafanaAnnotationError, GrafanaClient
from .influx import InfluxClient, InfluxError
from .modbus import ModbusError, SungrowModbusController
from .state import BatteryDecision, evaluate_soc, power_kw_to_register_value

logger = logging.getLogger(__name__)


@dataclass
class BalancerIterationResult:
    """Outcome of a single balancer check cycle."""

    soc: float
    decision: BatteryDecision
    modbus_written: bool
    grafana_annotated: bool
    inverter_results: dict[str, bool]
    grafana_annotation_id: int | None


class BatteryBalancer:
    """Orchestrator for Sungrow battery charge power balancing."""

    def __init__(
        self,
        config: Config,
        influx_client: InfluxClient | None = None,
        modbus_controller: SungrowModbusController | None = None,
        grafana_client: GrafanaClient | None = None,
    ) -> None:
        self.config = config
        self.current_power_kw: float | None = None
        self._stop_event = threading.Event()

        self.influx_client = influx_client or InfluxClient(
            url=config.influxdb_url,
            user=config.influxdb_user,
            password=config.influxdb_password,
            db=config.influxdb_db,
            query=config.influxdb_query,
            verify_ssl=config.influxdb_verify_ssl,
        )

        self.modbus_controller = modbus_controller or SungrowModbusController(
            inverter_hosts=config.inverter_hosts,
            register=config.modbus_register,
            register_offset=config.modbus_register_offset,
            slave_id=config.modbus_slave_id,
            timeout=config.modbus_timeout,
        )

        self.grafana_client = grafana_client or GrafanaClient(
            url=config.grafana_url,
            token=config.grafana_token,
            dashboard_uid=config.grafana_dashboard_uid,
            panel_id=config.grafana_panel_id,
        )

    def run_once(self, force_power_kw: float | None = None) -> BalancerIterationResult:
        """Execute a single monitoring and balancing cycle.

        Parameters:
            force_power_kw: Optional explicit power override (used in one-shot mode).

        Returns:
            BalancerIterationResult detailing the cycle outcome.
        """
        logger.debug("Starting balancer iteration...")

        # 1. Read SoC from InfluxDB
        soc = self.influx_client.fetch_battery_soc()
        logger.info("Current battery State-of-Charge (SoC): %.1f%%", soc)

        # 2. Evaluate State & Target Power
        if force_power_kw is not None:
            raw_val = power_kw_to_register_value(force_power_kw)
            decision = BatteryDecision(
                soc=soc,
                target_power_kw=force_power_kw,
                register_raw_value=raw_val,
                reason=f"Manual power override specified via CLI: {force_power_kw:.2f} kW.",
                state_changed=True,
            )
        else:
            decision = evaluate_soc(
                soc=soc,
                current_power_kw=self.current_power_kw,
                high_threshold=self.config.high_soc_threshold,
                low_threshold=self.config.low_soc_threshold,
                full_threshold=self.config.full_soc_threshold,
                reduced_power_kw=self.config.reduced_charging_power,
                max_power_kw=self.config.max_charging_power,
            )

        inverter_results: dict[str, bool] = {}
        modbus_written = False
        grafana_annotated = False
        annotation_id: int | None = None

        # 3. Apply changes if state changed or manual power was given
        if decision.state_changed:
            logger.info("State change detected: %s", decision.reason)

            # Write to Modbus on all inverters
            try:
                inverter_results = self.modbus_controller.write_max_charging_power(
                    power_kw=decision.target_power_kw,
                    dry_run=self.config.dry_run,
                )
                modbus_written = True
            except ModbusError as exc:
                logger.error("Failed to set Modbus max charging power: %s", exc)
                # Keep loop running, but do not record power as set if it failed
                raise

            # Post Grafana Annotation
            annotation_text = (
                f"**Sungrow Battery Balancer**\n\n"
                f"- **Max Charging Power:** `{decision.target_power_kw:.1f} kW`\n"
                f"- **Battery SoC:** `{soc:.1f}%`\n"
                f"- **Reason:** {decision.reason}"
            )
            try:
                annotation_id = self.grafana_client.create_annotation(
                    text=annotation_text,
                    dry_run=self.config.dry_run,
                )
                grafana_annotated = True
            except GrafanaAnnotationError as exc:
                logger.warning("Failed to create Grafana annotation (non-fatal): %s", exc)

            # Update tracked power
            self.current_power_kw = decision.target_power_kw
        else:
            logger.info(
                "SoC is %.1f%% (Charging power remains %.2f kW). No change required.",
                soc,
                decision.target_power_kw,
            )

        return BalancerIterationResult(
            soc=soc,
            decision=decision,
            modbus_written=modbus_written,
            grafana_annotated=grafana_annotated,
            inverter_results=inverter_results,
            grafana_annotation_id=annotation_id,
        )

    def run_one_shot(self) -> BalancerIterationResult:
        """Run in one-shot mode: print config, execute single cycle, and return."""
        print(print_config_summary(self.config))
        print("\n[One-Shot Mode] Querying InfluxDB and executing battery balancer cycle...\n")

        force_power = self.config.set_power
        result = self.run_once(force_power_kw=force_power)

        print("\n" + "=" * 70)
        print(" ONE-SHOT EXECUTION REPORT")
        print("=" * 70)
        print(f" Current Battery SoC      : {result.soc:.1f}%")
        print(f" Target Charging Power    : {result.decision.target_power_kw:.2f} kW")
        print(f" Decision Reason          : {result.decision.reason}")
        print(
            f" Modbus Register Written  : {'YES' if result.modbus_written else 'NO (Unchanged / Skipped)'}"
        )
        if result.inverter_results:
            print(" Inverter Results         :")
            for inv, success in result.inverter_results.items():
                print(f"   - {inv:<25}: {'SUCCESS' if success else 'FAILED'}")
        print(f" Grafana Annotation Set   : {'YES' if result.grafana_annotated else 'NO'}")
        if result.grafana_annotation_id:
            print(f" Grafana Annotation ID    : {result.grafana_annotation_id}")
        print("=" * 70 + "\n")

        return result

    def run_loop(self) -> None:
        """Run continuous monitoring loop with graceful signal handling and error recovery."""
        self._setup_signals()
        logger.info("Starting Sungrow Battery Balancer continuous monitoring loop.")
        logger.info(
            "Monitoring interval: %d seconds. Dry run: %s",
            self.config.check_interval,
            self.config.dry_run,
        )
        logger.info(
            "Configured Inverters: %s", ", ".join(str(h) for h in self.config.inverter_hosts)
        )

        while not self._stop_event.is_set():
            start_time = time.monotonic()
            try:
                self.run_once()
            except InfluxError as exc:
                logger.error(
                    "InfluxDB error during cycle: %s (will retry in %ds)",
                    exc,
                    self.config.check_interval,
                )
            except ModbusError as exc:
                logger.error(
                    "Modbus error during cycle: %s (will retry in %ds)",
                    exc,
                    self.config.check_interval,
                )
            except Exception:
                logger.exception("Unexpected error in monitoring cycle")

            elapsed = time.monotonic() - start_time
            sleep_duration = max(0.0, float(self.config.check_interval) - elapsed)
            if self._stop_event.wait(timeout=sleep_duration):
                break

        logger.info("Sungrow Battery Balancer loop stopped cleanly.")

    def stop(self) -> None:
        """Signal the balancer loop to stop."""
        self._stop_event.set()

    def _setup_signals(self) -> None:
        """Attach SIGINT and SIGTERM handlers for graceful shutdown."""

        def handler(sig, frame):  # pragma: no cover
            sig_name = signal.Signals(sig).name
            logger.info("Received shutdown signal %s. Exiting gracefully...", sig_name)
            self.stop()

        try:
            signal.signal(signal.SIGINT, handler)
            signal.signal(signal.SIGTERM, handler)
        except (ValueError, AttributeError):
            # Not in main thread or platform unsupported
            pass

    def close(self) -> None:
        """Close open client sessions."""
        self.influx_client.close()
        self.grafana_client.close()
