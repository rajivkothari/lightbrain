"""
Smoothing engine — EMA envelope followers that convert raw audio energy into
lighting-friendly, time-smoothed lane values.

Each lane has separate attack and decay time constants so fast transients
snap up quickly while tails fall off slowly, giving a natural feel.
"""

import math
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EnvelopeConfig:
    """Configuration for a single EMA envelope follower."""
    attack_ms: float   # How fast the value rises  (ms)
    decay_ms: float    # How fast the value falls   (ms)
    cooldown_ms: float # Minimum hold time at peak  (ms) — 0 = none
    min_threshold: float = 0.0  # Values below this are treated as 0
    max_threshold: float = 1.0  # Input is clamped to this before processing
    # Placeholder — curve shaping to be added later
    curve_type: str = "linear"


class EnvelopeFollower:
    """
    EMA envelope follower with separate attack/decay times.

    alpha = 1 - exp(-dt / tau)

    Rising signal  → use attack tau
    Falling signal → use decay tau
    """

    def __init__(self, config: EnvelopeConfig, initial_value: float = 0.0):
        self.config = config
        self.value: float = initial_value
        self._last_update: float = time.monotonic()
        self._peak_time:   float = 0.0    # timestamp of last rising edge
        self._peak_value:  float = 0.0    # smoothed value recorded at peak
        self._last_raw:    float = 0.0    # raw input from previous frame

    def update(self, raw: float, now: Optional[float] = None) -> float:
        """
        Feed a new raw value and return the smoothed output.

        raw — normalized 0.0–1.0 input value
        now — optional clock override (Sprint 3: pass frame.time_s for deterministic
              replay; omit for live operation to use time.monotonic())
        returns smoothed 0.0–1.0 output value
        """
        if now is None:
            now = time.monotonic()
        dt_s = now - self._last_update
        self._last_update = now

        # Clamp and apply threshold gate
        raw = max(0.0, min(raw, self.config.max_threshold))
        if raw < self.config.min_threshold:
            raw = 0.0

        # Choose time constant based on direction
        rising = raw >= self.value
        tau_ms = self.config.attack_ms if rising else self.config.decay_ms

        # Avoid divide-by-zero for very short or zero tau
        if tau_ms <= 0:
            self.value = raw
        else:
            tau_s = tau_ms / 1000.0
            alpha = 1.0 - math.exp(-dt_s / tau_s)
            self.value += alpha * (raw - self.value)

        self._last_raw = raw

        # Track the highest smoothed value reached: update peak whenever the
        # smoothed value is still climbing.  EMA rises over several frames, so
        # we must keep updating peak_value as long as self.value keeps growing —
        # not just on the single frame where raw first jumped.
        if self.value > self._peak_value:
            self._peak_value = self.value
            self._peak_time  = now

        # Cooldown: for cooldown_ms after the last new peak, prevent decay
        # below peak_value so the lane holds at its maximum before releasing.
        if self.config.cooldown_ms > 0:
            elapsed_since_peak = (now - self._peak_time) * 1000.0
            if elapsed_since_peak < self.config.cooldown_ms:
                self.value = max(self.value, self._peak_value)

        self.value = max(0.0, min(1.0, self.value))
        return self.value

    def reset(self, value: float = 0.0, now: Optional[float] = None) -> None:
        """Reset follower state. Pass now to initialize clock for deterministic use."""
        t = now if now is not None else time.monotonic()
        self.value        = value
        self._last_update = t
        self._peak_time   = t
        self._peak_value  = 0.0
        self._last_raw    = 0.0


# ---------------------------------------------------------------------------
# Pre-defined lane configs (Sprint 1: Impact + Room active; others ready)
# ---------------------------------------------------------------------------

IMPACT_CONFIG = EnvelopeConfig(
    attack_ms=10,
    decay_ms=250,
    cooldown_ms=250,
    min_threshold=0.15,
)

ROOM_CONFIG = EnvelopeConfig(
    attack_ms=600,
    decay_ms=3000,
    cooldown_ms=0,
    min_threshold=0.05,
)

FLOOR_CONFIG = EnvelopeConfig(
    attack_ms=150,
    decay_ms=900,
    cooldown_ms=150,
    min_threshold=0.08,
)

BEAM_CONFIG = EnvelopeConfig(
    attack_ms=80,
    decay_ms=2000,
    cooldown_ms=1000,
    min_threshold=0.10,
)

SPARKLE_CONFIG = EnvelopeConfig(
    attack_ms=40,
    decay_ms=400,
    cooldown_ms=150,
    min_threshold=0.10,
)


class LaneSmoother:
    """
    Holds one EnvelopeFollower per lighting lane and exposes named accessors.

    Sprint 1: impact + room are active.
    floor / beam / sparkle followers are created but not yet wired to a lane.
    """

    def __init__(self):
        self.impact  = EnvelopeFollower(IMPACT_CONFIG)
        self.room    = EnvelopeFollower(ROOM_CONFIG)
        self.floor   = EnvelopeFollower(FLOOR_CONFIG)   # TODO Sprint 2
        self.beam    = EnvelopeFollower(BEAM_CONFIG)     # TODO Sprint 2
        self.sparkle = EnvelopeFollower(SPARKLE_CONFIG)  # TODO Sprint 2

    def update(self, bands: dict, now: Optional[float] = None) -> dict:
        """
        Update all lane followers from the raw audio band dict.

        bands keys: low_energy, mid_energy, high_energy, overall_energy
        now   — optional clock override for deterministic replay (Sprint 3)
        returns dict of smoothed lane values
        """
        overall = bands.get("overall_energy", 0.0)
        low     = bands.get("low_energy",     0.0)
        mid     = bands.get("mid_energy",     0.0)
        high    = bands.get("high_energy",    0.0)

        # Impact tracks instantaneous transients — use low+overall blend
        impact_raw = low * 0.6 + overall * 0.4

        # Room tracks long-term energy — blend all bands
        room_raw = low * 0.4 + mid * 0.35 + high * 0.25

        return {
            "impact":  self.impact.update(impact_raw, now=now),
            "room":    self.room.update(room_raw,    now=now),
            "floor":   self.floor.update(low,        now=now),
            "beam":    self.beam.update(mid,          now=now),
            "sparkle": self.sparkle.update(high,      now=now),
        }

    def reset_all(self, now: Optional[float] = None) -> None:
        """Reset all followers. Pass now to initialize clocks for deterministic use."""
        for follower in (self.impact, self.room, self.floor,
                         self.beam, self.sparkle):
            follower.reset(now=now)
