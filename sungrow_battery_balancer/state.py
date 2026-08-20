"""State evaluation and hysteresis decision logic for battery charging power."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class BatteryDecision:
    """Represents a computed charging power decision based on SoC."""

    soc: float
    target_power_kw: float
    register_raw_value: int
    reason: str
    state_changed: bool


def power_kw_to_register_value(power_kw: float) -> int:
    """Convert power in kilowatts (kW) to Sungrow Modbus register raw value (0.01 kW units).

    Example:
        1.0 kW -> 100
        10.6 kW -> 1060
    """
    if power_kw < 0:
        raise ValueError(f"Charging power cannot be negative: {power_kw}")
    raw_val = round(power_kw * 100)
    if raw_val > 65535:
        raise ValueError(f"Charging power raw value exceeds 16-bit unsigned range: {raw_val}")
    return raw_val


def register_value_to_power_kw(raw_val: int) -> float:
    """Convert Sungrow Modbus register raw value (0.01 kW units) to kilowatts (kW)."""
    return round(raw_val * 0.01, 2)


def evaluate_soc(
    soc: float,
    current_power_kw: float | None,
    high_threshold: float = 85.0,
    low_threshold: float = 80.0,
    full_threshold: float = 100.0,
    reduced_power_kw: float = 1.0,
    max_power_kw: float = 10.6,
) -> BatteryDecision:
    """Evaluate battery state-of-charge (SoC) and determine target max charging power.

    Rules:
      1. SoC >= full_threshold (100.0%) -> Restore max charging power (10.6 kW).
      2. SoC >= high_threshold (85.0%) -> Throttle charging power to reduced_power (1.0 kW).
      3. SoC < low_threshold (80.0%) -> Restore max charging power (10.6 kW).
      4. low_threshold <= SoC < high_threshold (80.0% - 85.0%): Hysteresis band.
         - If current_power_kw is known: maintain current_power_kw.
         - If uninitialized (current_power_kw is None): default to max_power_kw.

    Returns:
      BatteryDecision object containing the target power, raw register value,
      human-readable reason, and a boolean indicating whether the state changed.
    """
    if soc >= full_threshold:
        target_power = max_power_kw
        reason = (
            f"Battery SoC is full ({soc:.1f}% >= {full_threshold:.1f}%). "
            f"Setting max charging power to {max_power_kw:.1f} kW."
        )
    elif soc >= high_threshold:
        target_power = reduced_power_kw
        reason = (
            f"Battery SoC is high ({soc:.1f}% >= {high_threshold:.1f}%). "
            f"Throttling max charging power to {reduced_power_kw:.1f} kW."
        )
    elif soc < low_threshold:
        target_power = max_power_kw
        reason = (
            f"Battery SoC is low ({soc:.1f}% < {low_threshold:.1f}%). "
            f"Setting max charging power to {max_power_kw:.1f} kW."
        )
    else:
        # In hysteresis deadband [low_threshold, high_threshold)
        if current_power_kw is not None:
            target_power = current_power_kw
            reason = (
                f"Battery SoC ({soc:.1f}%) is in hysteresis band "
                f"[{low_threshold:.1f}%, {high_threshold:.1f}%). "
                f"Maintaining current charging power at {current_power_kw:.1f} kW."
            )
        else:
            target_power = max_power_kw
            reason = (
                f"Battery SoC ({soc:.1f}%) is in hysteresis band "
                f"[{low_threshold:.1f}%, {high_threshold:.1f}%) with no previous state. "
                f"Initializing charging power to {max_power_kw:.1f} kW."
            )

    register_raw = power_kw_to_register_value(target_power)
    state_changed = current_power_kw is None or not math.isclose(
        current_power_kw, target_power, abs_tol=0.001
    )

    return BatteryDecision(
        soc=soc,
        target_power_kw=target_power,
        register_raw_value=register_raw,
        reason=reason,
        state_changed=state_changed,
    )
