"""
Beat detector — energy-flux onset detection for palette transition triggers.

Uses a sliding window of low-band energy to detect transient peaks.
Fires when the current frame exceeds the rolling average by a configurable
threshold, with a minimum inter-beat interval to prevent double-triggering.

Exports:
  BeatDetector  — stateful onset detector with BPM estimation
"""

import time
from collections import deque
from typing import Tuple


class BeatDetector:
    """
    Energy-threshold onset detector with BPM estimation.

    Call update() each audio frame with the current low-band energy.
    Returns (beat_detected: bool, beat_strength: float 0–1).

    beat_strength is the fractional excess above the rolling average,
    clamped to 0.0–1.0. Use it as an intensity multiplier for effects.

    BPM is estimated from the interval between the last 8 beats and
    exposed via the .bpm property.
    """

    def __init__(
        self,
        history_size: int = 20,
        threshold: float = 1.5,
        min_interval_ms: float = 200.0,
    ):
        """
        history_size    — frames to average for baseline (20 @ 40 fps ≈ 500 ms)
        threshold       — beat fires when energy > threshold × average
        min_interval_ms — minimum time between beats (200 ms → max 300 BPM)
        """
        self._history        = deque(maxlen=history_size)
        self._threshold      = threshold
        self._min_interval   = min_interval_ms / 1000.0
        self._last_beat_time = 0.0
        self._beat_intervals: deque = deque(maxlen=8)
        self.bpm: float      = 0.0

    def update(self, low_energy: float) -> Tuple[bool, float]:
        """
        Feed current low-band energy; return (beat_detected, beat_strength).

        Returns (False, 0.0) until history warms up or energy is below threshold.
        """
        now = time.monotonic()

        if not self._history:
            self._history.append(low_energy)
            return False, 0.0

        avg = sum(self._history) / len(self._history)
        self._history.append(low_energy)

        if avg >= 0.01 and low_energy > self._threshold * avg:
            elapsed = now - self._last_beat_time
            if elapsed >= self._min_interval:
                if self._last_beat_time > 0:
                    self._beat_intervals.append(elapsed)
                    if len(self._beat_intervals) >= 2:
                        mean_ivl = sum(self._beat_intervals) / len(self._beat_intervals)
                        self.bpm = 60.0 / mean_ivl if mean_ivl > 0 else 0.0
                self._last_beat_time = now
                strength = min(1.0, low_energy / max(avg, 1e-6) - 1.0)
                return True, strength

        return False, 0.0

    def reset(self) -> None:
        """Clear all history and reset BPM."""
        self._history.clear()
        self._beat_intervals.clear()
        self._last_beat_time = 0.0
        self.bpm = 0.0
