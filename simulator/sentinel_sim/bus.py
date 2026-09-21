"""Synthetic physics bus: EPS (battery/bus) and reaction wheels with redundant sensors.

Why this exists (ADR 0004): SMAP/MSL channels are anonymized and have no redundancy or physics, so
cross-channel checks need a telemetry family where the relations are known. The *degradation
trajectories* are real (PCoE battery capacity/resistance, IMS bearing vibration); everything around
them is a simplified, documented model. **Every channel here is synthetic.**

Simplifications (documented, deliberate)
* Battery pack = ``N_SERIES`` x ``N_PARALLEL`` PCoE cells; OCV is linear, 3.0-4.2 V per cell.
* Terminal voltage = OCV - I * R_pack, with R from the cell's real electrolyte resistance ``Re``.
* Charge controller: solar is curtailed at full charge; the payload sheds below 45 % SoC.
* Wheel vibration is the IMS accelerometer RMS of one bearing (in g) with independent sensor noise.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from sentinel_core.timebase import STEP_SECONDS, rng_for

from .pcoe import BatteryTrajectory, WheelTrajectory

N_SERIES = 8
N_PARALLEL = 20
ORBIT_STEPS = 90  # 90 min orbit at 60 s/step
SUN_FRACTION = 0.62
SOLAR_PEAK_A = 10.0
LOAD_BASE_A = 3.0
LOAD_PAYLOAD_A = 2.0
BUS_NOMINAL_V = 28.0

EPS_CHANNELS = ("batt_v_a", "batt_v_b", "batt_i", "batt_t", "bus_v", "solar_i", "load_i")
ADCS_CHANNELS = (
    "rw1_speed",
    "rw1_temp",
    "rw1_vib_a",
    "rw1_vib_b",
    "rw2_speed",
    "rw2_temp",
    "rw2_vib",
)
BUS_CHANNELS = EPS_CHANNELS + ADCS_CHANNELS
UNITS = {
    "batt_v_a": "V", "batt_v_b": "V", "batt_i": "A", "batt_t": "degC", "bus_v": "V",
    "solar_i": "A", "load_i": "A", "rw1_speed": "rpm", "rw1_temp": "degC", "rw1_vib_a": "g",
    "rw1_vib_b": "g", "rw2_speed": "rpm", "rw2_temp": "degC", "rw2_vib": "g",
}  # fmt: skip

# Aging progress (battery, wheel), each in [0, 1]; scenarios time-compress real degradation.
AgingFn = Callable[[int], tuple[float, float]]


def healthy_aging(_k: int) -> tuple[float, float]:
    """Nominal: a lightly used battery and the healthy early portion of the bearing run."""
    return 0.05, 0.05


@dataclass(frozen=True, slots=True)
class BusSample:
    values: dict[str, float]  # what the sensors report (goes into telemetry)
    truth: dict[str, float]  # the true physical state (never transmitted; for evaluation)


class PhysicsBus:
    def __init__(
        self,
        seed: int,
        battery: BatteryTrajectory,
        wheel: WheelTrajectory,
        aging: AgingFn = healthy_aging,
    ) -> None:
        self.seed = seed
        self.battery = battery
        self.wheel = wheel
        self.aging = aging
        self.synthetic_sources = {"battery": battery.source, "wheel": wheel.source}
        self._early_rms0 = float(wheel.rms[: max(1, len(wheel.rms) // 10), 0].mean())
        self.reset()

    def reset(self) -> None:
        self._noise = rng_for(self.seed, "bus-noise")
        self._sched = rng_for(self.seed, "bus-schedule")
        self.soc = 0.90
        self.temp = 22.0
        self._payload_left = 0
        self._next_payload_in = int(self._sched.integers(20, 120))

    # -- helpers -----------------------------------------------------------------------
    def _payload_on(self, soc: float) -> bool:
        """Payload runs in bursts of 20-60 steps every 60-200 steps; sheds when SoC is low."""
        if self._payload_left > 0:
            self._payload_left -= 1
            return soc > 0.45
        self._next_payload_in -= 1
        if self._next_payload_in <= 0:
            self._payload_left = int(self._sched.integers(20, 60))
            self._next_payload_in = int(self._sched.integers(60, 200))
        return False

    def _vibration(self, progress: float, bearing: int) -> float:
        n = len(self.wheel.rms)
        idx = min(n - 1, max(0, round(progress * (n - 1))))
        return float(self.wheel.rms[idx, bearing])

    # -- one step ----------------------------------------------------------------------
    def step(self, k: int) -> BusSample:
        """Advance one 60 s step and return sensor readings plus ground truth.

        Steps must be requested in order (state is integrated); use :meth:`reset` to restart.
        """
        pb, pw = self.aging(k)
        n = self._noise
        phase = (k % ORBIT_STEPS) / ORBIT_STEPS
        sun = math.sin(math.pi * phase / SUN_FRACTION) if phase < SUN_FRACTION else 0.0
        solar = SOLAR_PEAK_A * max(0.0, sun) * (1.0 + n.normal(0, 0.01))
        payload = self._payload_on(self.soc)
        load = LOAD_BASE_A + (LOAD_PAYLOAD_A if payload else 0.0) + n.normal(0, 0.03)

        batt_i = solar - load  # +charge / -discharge
        if self.soc >= 0.98:
            batt_i = min(batt_i, 0.0)  # charge controller curtails solar at full charge
        solar = load + batt_i  # delivered solar current: solar_i - load_i == batt_i holds exactly
        cap_ah = self.battery.capacity0_ah * self.battery.capacity_at(pb) * N_PARALLEL
        self.soc = min(1.0, max(0.0, self.soc + batt_i * (STEP_SECONDS / 3600.0) / cap_ah))

        r_pack = self.battery.resistance_at(pb) * N_SERIES / N_PARALLEL
        ocv = N_SERIES * (3.0 + 1.2 * self.soc)
        true_v = ocv + batt_i * r_pack  # charging raises terminal voltage, discharging lowers it

        target_t = 20.0 + 1.1 * abs(batt_i) + 0.6 * load
        self.temp += 0.08 * (target_t - self.temp)

        vib0 = self._vibration(pw, 0)
        vib1 = self._vibration(pw, 1)
        vib_ratio = vib0 / self._early_rms0 if self._early_rms0 > 0 else 1.0
        speed1 = 3000.0 + 15.0 * math.sin(2 * math.pi * k / 500.0)
        speed2 = 3100.0 + 12.0 * math.sin(2 * math.pi * k / 430.0 + 1.0)

        values = {
            "batt_v_a": true_v + n.normal(0, 0.02),
            "batt_v_b": true_v + 0.05 + n.normal(0, 0.02),  # fixed 50 mV calibration offset
            "batt_i": batt_i + n.normal(0, 0.03),
            "batt_t": self.temp + n.normal(0, 0.15),
            "bus_v": BUS_NOMINAL_V + 0.08 * math.sin(2 * math.pi * k / 7.0) + n.normal(0, 0.02),
            "solar_i": solar + n.normal(0, 0.03),
            "load_i": load + n.normal(0, 0.03),
            "rw1_speed": speed1 + n.normal(0, 2.0),
            "rw1_temp": 35.0 + 8.0 * (vib_ratio - 1.0) + n.normal(0, 0.2),
            "rw1_vib_a": vib0 * (1.0 + n.normal(0, 0.03)),
            "rw1_vib_b": vib0 * (1.0 + n.normal(0, 0.03)),
            "rw2_speed": speed2 + n.normal(0, 2.0),
            "rw2_temp": 36.0 + n.normal(0, 0.2),
            "rw2_vib": vib1 * (1.0 + n.normal(0, 0.03)),
        }
        truth = {
            "batt_v": true_v, "batt_i": batt_i, "soc": self.soc, "payload_on": float(payload),
            "cap_ah": cap_ah, "rw1_vib": vib0, "aging_battery": pb, "aging_wheel": pw,
        }  # fmt: skip
        return BusSample(values, truth)
