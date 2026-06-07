"""
StrobeEngine — rise-synchronized strobe for EDM build-ups.

Activates in banger / indian_latin modes when high-frequency energy rises
above a threshold, creating the classic "lift" effect before the drop:

    low energy → 2 Hz slow flash   (just above threshold)
    mid energy → 8 Hz medium flash
    full energy → 16 Hz rapid fire

The DMX strobe channel (Ch8 on RockWedge) mirrors the rate, so the
physical fixture speed also escalates with the music.
"""

import time
from typing import Optional, Tuple


_STROBE_MODES  = frozenset({"banger", "indian_latin"})
_THRESHOLD     = 0.55    # high_energy must exceed this to activate
_MIN_FREQ      =  2.0    # Hz at threshold
_MAX_FREQ      = 16.0    # Hz at full energy
_DUTY_CYCLE    = 0.25    # fraction of each period the fixture fires ("on" time)
_HOLD_SECS     = 0.22    # brief hold after energy drops — prevents cutout at drop


class StrobeEngine:
    """
    Time-based oscillator for EDM lift sections.

    Call update() once per audio frame.  Returns a 3-tuple:
        strobe_on  (bool)   True during the on-phase of the current cycle
        rate       (float)  0–1, proportional to strobe speed (for DMX)
        freq_hz    (float)  current oscillator frequency
    """

    def __init__(self) -> None:
        self._phase:  float = 0.0
        self._hold_t: float = 0.0
        self._last_t: float = time.monotonic()

    def update(
        self,
        high_energy: float,
        mode_key:    str,
        now:         Optional[float] = None,
    ) -> Tuple[bool, float, float]:
        if now is None:
            now = time.monotonic()
        dt = min(now - self._last_t, 0.1)
        self._last_t = now

        above = mode_key in _STROBE_MODES and high_energy >= _THRESHOLD

        # Refresh hold timer while energy is up; run it down once it drops
        if above:
            self._hold_t = _HOLD_SECS
        elif self._hold_t > 0.0:
            self._hold_t = max(0.0, self._hold_t - dt)

        active = above or self._hold_t > 0.0

        if not active:
            self._phase = 0.0
            return False, 0.0, 0.0

        # Normalised position above threshold (0 at threshold, 1 at max energy)
        t    = min(1.0, (high_energy - _THRESHOLD) / max(1e-6, 1.0 - _THRESHOLD))
        freq = _MIN_FREQ + t * (_MAX_FREQ - _MIN_FREQ)

        self._phase = (self._phase + dt * freq) % 1.0
        strobe_on   = self._phase < _DUTY_CYCLE

        # Ensure rate stays non-zero while hold is active so DMX doesn't cut early
        rate = max(0.04, t)

        return strobe_on, rate, freq

    def reset(self) -> None:
        """Force-reset timing state — useful for deterministic tests."""
        self._phase  = 0.0
        self._hold_t = 0.0
        self._last_t = time.monotonic()
