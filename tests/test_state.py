"""Tests for state evaluation, power conversion, and hysteresis decision logic."""

import pytest

from sungrow_battery_balancer.state import (
    evaluate_soc,
    power_kw_to_register_value,
    register_value_to_power_kw,
)


class TestPowerConversions:
    def test_power_kw_to_register_value(self):
        assert power_kw_to_register_value(1.0) == 100
        assert power_kw_to_register_value(10.6) == 1060
        assert power_kw_to_register_value(0.0) == 0
        assert power_kw_to_register_value(5.55) == 555

    def test_register_value_to_power_kw(self):
        assert register_value_to_power_kw(100) == 1.0
        assert register_value_to_power_kw(1060) == 10.6
        assert register_value_to_power_kw(0) == 0.0

    def test_invalid_power_values(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            power_kw_to_register_value(-1.0)

        with pytest.raises(ValueError, match="exceeds 16-bit unsigned range"):
            power_kw_to_register_value(1000.0)  # 100,000 > 65535


class TestEvaluateSoc:
    def test_soc_below_low_threshold(self):
        # 27.3% SoC -> Max power 10.6 kW
        decision = evaluate_soc(soc=27.3, current_power_kw=None)
        assert decision.target_power_kw == 10.6
        assert decision.register_raw_value == 1060
        assert decision.state_changed is True
        assert "low" in decision.reason.lower()

    def test_soc_at_high_threshold(self):
        # Exactly 85.0% -> Reduced power 1.0 kW
        decision = evaluate_soc(soc=85.0, current_power_kw=10.6)
        assert decision.target_power_kw == 1.0
        assert decision.register_raw_value == 100
        assert decision.state_changed is True
        assert "throttling" in decision.reason.lower()

    def test_soc_above_high_threshold(self):
        # 92.5% SoC -> Reduced power 1.0 kW
        decision = evaluate_soc(soc=92.5, current_power_kw=10.6)
        assert decision.target_power_kw == 1.0
        assert decision.state_changed is True

    def test_soc_at_full_threshold_first_time(self):
        # 100.0% SoC when previous power was 1.0 kW -> State changed to 10.6 kW
        decision = evaluate_soc(soc=100.0, current_power_kw=1.0)
        assert decision.target_power_kw == 10.6
        assert decision.register_raw_value == 1060
        assert decision.state_changed is True
        assert "setting max charging power" in decision.reason.lower()

    def test_soc_remains_at_full_consecutive_cycle(self):
        # 100.0% SoC when previous power was ALREADY 10.6 kW -> State NOT changed
        decision = evaluate_soc(soc=100.0, current_power_kw=10.6)
        assert decision.target_power_kw == 10.6
        assert decision.register_raw_value == 1060
        assert decision.state_changed is False
        assert "already set" in decision.reason.lower()

    def test_hysteresis_when_charging_up(self):
        # Previous state was 10.6 kW, SoC rises to 82.0% (between 80% and 85%) -> stays 10.6 kW
        decision = evaluate_soc(soc=82.0, current_power_kw=10.6)
        assert decision.target_power_kw == 10.6
        assert decision.state_changed is False
        assert "maintaining" in decision.reason.lower()

    def test_hysteresis_when_discharging_down(self):
        # Previous state was 1.0 kW, SoC drops to 82.0% (between 80% and 85%) -> stays 1.0 kW
        decision = evaluate_soc(soc=82.0, current_power_kw=1.0)
        assert decision.target_power_kw == 1.0
        assert decision.state_changed is False
        assert "maintaining" in decision.reason.lower()

    def test_hysteresis_drop_below_80(self):
        # Previous state was 1.0 kW, SoC drops below 80.0% to 79.9% -> restores 10.6 kW
        decision = evaluate_soc(soc=79.9, current_power_kw=1.0)
        assert decision.target_power_kw == 10.6
        assert decision.state_changed is True
        assert "low" in decision.reason.lower()

    def test_hysteresis_uninitialized(self):
        # No previous state and SoC is in 80-85% band -> defaults to max power 10.6 kW
        decision = evaluate_soc(soc=82.5, current_power_kw=None)
        assert decision.target_power_kw == 10.6
        assert decision.state_changed is True
        assert "initializing" in decision.reason.lower()

    def test_custom_thresholds(self):
        decision = evaluate_soc(
            soc=91.0,
            current_power_kw=12.0,
            high_threshold=90.0,
            low_threshold=70.0,
            full_threshold=98.0,
            reduced_power_kw=2.0,
            max_power_kw=12.0,
        )
        assert decision.target_power_kw == 2.0
        assert decision.register_raw_value == 200
        assert decision.state_changed is True
