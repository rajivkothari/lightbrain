"""
Smoothing engine — EMA envelope followers that convert raw audio energy into
lighting-friendly, time-smoothed lane values.

Each lane has separate attack and decay time constants so fast transients
snap up quickly while tails fall off slowly, giving a natural feel.

Physics notes for physical fixtures
------------------------------------
The asymmetric EMA model compensates for real-world fixture behavior:

  - LED driver rise/fall inertia: cheap PWM drivers add implicit lag at
    the bottom of the dimming curve.  A fast attack_ms lets the signal
    outrun the driver's own ramp.
  - Moving head yoke inertia: mechanical mass needs heavily dampened decay
    to avoid overshoot/wobble on stepper motors.
  - Human visual persistence: a sharp flash registers in ~10-20ms but the
    after-image lingers.  Matching decay to attack creates nervous blinking;
    a slower decay produces cohesive sparkle texture.

Alpha coefficients from time constant:
    alpha_attack = 1 - exp(-dt / tau_attack)
    alpha_decay  = 1 - exp(-dt / tau_decay)

Directional selection per frame:
    Y_t = alpha_attack * X_t + (1 - alpha_attack) * Y_{t-1}    if X_t >= Y_{t-1}
    Y_t = alpha_decay  * X_t + (1 - alpha_decay)  * Y_{t-1}    if X_t <  Y_{t-1}
"""

import math
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

# Smoothed values below this are forced to exactly 0.0.
# Prevents subnormal float operations and fixtures stuck at DMX 1-2
# from an exponential decay that mathematically never reaches zero.
# At gamma 2.2, DMX=1 maps to ~0.0003 relative luminance — invisible,
# but the fixture's minimum PWM pulse may still be perceptible as a faint glow.
_DECAY_FLOOR: float = 0.003  # ~= 1/255 / gamma headroom


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

    Rising signal  → use attack tau  (transient dominance: instantly overrides decay)
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

        Transient dominance: if raw >= self.value (a new hit arrives during an
        active decay), the attack alpha immediately takes over from the current
        Y_{t-1}.  There is no discontinuity — the EMA formula is continuous
        in both directions.
        """
        if now is None:
            now = time.monotonic()
        dt_s = max(0.0, now - self._last_update)
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
            else:
                self._peak_value = self.value

        self.value = max(0.0, min(1.0, self.value))

        # Decay floor: force to 0.0 when the smoothed value has decayed
        # below the perceptual threshold.  Prevents fixtures from lingering
        # at DMX 1-2 indefinitely and eliminates subnormal float arithmetic.
        if self.value < _DECAY_FLOOR and raw == 0.0:
            self.value = 0.0

        return self.value

    def reconfigure(self, config: EnvelopeConfig) -> None:
        """Swap EMA config without resetting state (smooth transition on mode switch)."""
        self.config = config

    def reset(self, value: float = 0.0, now: Optional[float] = None) -> None:
        """Reset follower state. Pass now to initialize clock for deterministic use."""
        t = now if now is not None else time.monotonic()
        self.value        = value
        self._last_update = t
        self._peak_time   = t
        self._peak_value  = 0.0
        self._last_raw    = 0.0


# ---------------------------------------------------------------------------
# Default lane configs (used when no mode-specific profile is defined)
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

# ---------------------------------------------------------------------------
# Per-mode EMA profiles
#
# Each mode can override any lane config.  Omitted lanes inherit defaults.
# Tuning rationale per mode:
#   - dinner/speech/slow_dance: long attack + very long decay = glacial drift,
#     no perceptible flicker.  Fixtures read like candlelight, not a nightclub.
#   - banger: very fast attack, moderate decay.  Impact lane has near-zero
#     attack for strobe-grade snap on kick drums.
#   - indian_latin: fast attack for tabla/dhol transients, longer decay for
#     sustained melodic phrases.
#   - open_dance: balanced middle ground.
# ---------------------------------------------------------------------------

@dataclass
class ModeEMAProfile:
    """Per-mode EMA overrides for each lane."""
    impact:  EnvelopeConfig
    room:    EnvelopeConfig
    floor:   EnvelopeConfig
    beam:    EnvelopeConfig
    sparkle: EnvelopeConfig


MODE_EMA_PROFILES: Dict[str, ModeEMAProfile] = {
    "dinner": ModeEMAProfile(
        impact  = EnvelopeConfig(attack_ms=80,   decay_ms=600,   cooldown_ms=400,  min_threshold=0.20),
        room    = EnvelopeConfig(attack_ms=1200,  decay_ms=5000,  cooldown_ms=0,    min_threshold=0.05),
        floor   = EnvelopeConfig(attack_ms=300,   decay_ms=1500,  cooldown_ms=200,  min_threshold=0.10),
        beam    = EnvelopeConfig(attack_ms=200,   decay_ms=3000,  cooldown_ms=1500, min_threshold=0.12),
        sparkle = EnvelopeConfig(attack_ms=100,   decay_ms=800,   cooldown_ms=200,  min_threshold=0.12),
    ),
    "speech": ModeEMAProfile(
        impact  = EnvelopeConfig(attack_ms=100,  decay_ms=800,   cooldown_ms=500,  min_threshold=0.25),
        room    = EnvelopeConfig(attack_ms=1500, decay_ms=6000,  cooldown_ms=0,    min_threshold=0.05),
        floor   = EnvelopeConfig(attack_ms=400,  decay_ms=2000,  cooldown_ms=300,  min_threshold=0.10),
        beam    = EnvelopeConfig(attack_ms=250,  decay_ms=4000,  cooldown_ms=2000, min_threshold=0.15),
        sparkle = EnvelopeConfig(attack_ms=120,  decay_ms=1000,  cooldown_ms=250,  min_threshold=0.15),
    ),
    "slow_dance": ModeEMAProfile(
        impact  = EnvelopeConfig(attack_ms=60,   decay_ms=500,   cooldown_ms=350,  min_threshold=0.18),
        room    = EnvelopeConfig(attack_ms=1000, decay_ms=4500,  cooldown_ms=0,    min_threshold=0.05),
        floor   = EnvelopeConfig(attack_ms=250,  decay_ms=1200,  cooldown_ms=200,  min_threshold=0.08),
        beam    = EnvelopeConfig(attack_ms=150,  decay_ms=3000,  cooldown_ms=1200, min_threshold=0.10),
        sparkle = EnvelopeConfig(attack_ms=80,   decay_ms=600,   cooldown_ms=200,  min_threshold=0.10),
    ),
    "open_dance": ModeEMAProfile(
        impact  = IMPACT_CONFIG,
        room    = ROOM_CONFIG,
        floor   = FLOOR_CONFIG,
        beam    = BEAM_CONFIG,
        sparkle = SPARKLE_CONFIG,
    ),
    "banger": ModeEMAProfile(
        impact  = EnvelopeConfig(attack_ms=5,    decay_ms=180,   cooldown_ms=200,  min_threshold=0.12),
        room    = EnvelopeConfig(attack_ms=400,  decay_ms=2000,  cooldown_ms=0,    min_threshold=0.05),
        floor   = EnvelopeConfig(attack_ms=100,  decay_ms=600,   cooldown_ms=120,  min_threshold=0.08),
        beam    = EnvelopeConfig(attack_ms=50,   decay_ms=1500,  cooldown_ms=800,  min_threshold=0.08),
        sparkle = EnvelopeConfig(attack_ms=25,   decay_ms=300,   cooldown_ms=100,  min_threshold=0.08),
    ),
    "indian_latin": ModeEMAProfile(
        impact  = EnvelopeConfig(attack_ms=8,    decay_ms=220,   cooldown_ms=230,  min_threshold=0.14),
        room    = EnvelopeConfig(attack_ms=500,  decay_ms=2500,  cooldown_ms=0,    min_threshold=0.05),
        floor   = EnvelopeConfig(attack_ms=120,  decay_ms=800,   cooldown_ms=140,  min_threshold=0.08),
        beam    = EnvelopeConfig(attack_ms=60,   decay_ms=1800,  cooldown_ms=900,  min_threshold=0.10),
        sparkle = EnvelopeConfig(attack_ms=30,   decay_ms=350,   cooldown_ms=130,  min_threshold=0.10),
    ),
}


def get_mode_ema_profile(mode_key: str) -> ModeEMAProfile:
    """Return the EMA profile for a mode, falling back to open_dance defaults."""
    return MODE_EMA_PROFILES.get(mode_key, MODE_EMA_PROFILES["open_dance"])


class LaneSmoother:
    """
    Holds one EnvelopeFollower per lighting lane and exposes named accessors.

    Call apply_mode_profile() on mode switch to reconfigure EMA coefficients
    without resetting state — the smoothed values continue from where they
    are, but attack/decay rates change to match the new mode's character.
    """

    def __init__(self, mode_key: str = "open_dance"):
        profile = get_mode_ema_profile(mode_key)
        self.impact  = EnvelopeFollower(profile.impact)
        self.room    = EnvelopeFollower(profile.room)
        self.floor   = EnvelopeFollower(profile.floor)
        self.beam    = EnvelopeFollower(profile.beam)
        self.sparkle = EnvelopeFollower(profile.sparkle)

    def apply_mode_profile(self, mode_key: str) -> None:
        """
        Swap all lane EMA configs to match the given mode.

        Does NOT reset smoothed values — the transition is continuous.
        A dinner→banger switch tightens the attack immediately so the next
        beat hits with banger-grade snap, but the room lane doesn't jump
        because its current smoothed value is preserved.
        """
        profile = get_mode_ema_profile(mode_key)
        self.impact.reconfigure(profile.impact)
        self.room.reconfigure(profile.room)
        self.floor.reconfigure(profile.floor)
        self.beam.reconfigure(profile.beam)
        self.sparkle.reconfigure(profile.sparkle)

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
