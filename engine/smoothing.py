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
        self._peak_time: float = 0.0   # timestamp of last peak
        self._last_raw: float = 0.0

    def update(self, raw: float) -> float:
        """
        Feed a new raw value and return the smoothed output.

        raw   — normalized 0.0–1.0 input value
        returns smoothed 0.0–1.0 output value
        """
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

        # Track peak time for cooldown logic
        if raw > self._last_raw:
            self._peak_time = now
        self._last_raw = raw

        # Cooldown: if we're inside the cooldown window, don't decay below
        # the value we had at peak (only relevant when using cooldown_ms > 0)
        if self.config.cooldown_ms > 0:
            elapsed_since_peak = (now - self._peak_time) * 1000.0
            if elapsed_since_peak < self.config.cooldown_ms:
                # Don't let value drop during cooldown window
                self.value = max(self.value, self._last_raw)

        self.value = max(0.0, min(1.0, self.value))
        return self.value

    def reset(self, value: float = 0.0) -> None:
        self.value = value
        self._last_update = time.monotonic()


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

    def update(self, bands: dict) -> dict:
        """
        Update all lane followers from the raw audio band dict.

        bands keys: low_energy, mid_energy, high_energy, overall_energy
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
            "impact":  self.impact.update(impact_raw),
            "room":    self.room.update(room_raw),
            "floor":   self.floor.update(low),           # TODO Sprint 2
            "beam":    self.beam.update(mid),             # TODO Sprint 2
            "sparkle": self.sparkle.update(high),         # TODO Sprint 2
        }

    def reset_all(self) -> None:
        for follower in (self.impact, self.room, self.floor,
                         self.beam, self.sparkle):
            follower.reset()
