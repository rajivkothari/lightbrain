"""
Synthetic audio source for testing without real audio hardware.

Generates a numpy audio block that mimics DJ music:
  - Bass sine wave  (~80 Hz)  with slow LFO envelope
  - Kick transients (40 Hz)   periodic sharp pulses
  - Mid sine wave   (~800 Hz) with medium LFO
  - Hi-hat noise    (6+ kHz)  filtered white noise bursts

All levels are shaped over time so the pipeline sees varying energies
in each band, which exercises the full smoothing and palette blend chain.

Compatible with AudioCapture.get_latest_block() — returns numpy float32
arrays of shape (block_size, 1) so the analyzer can process them directly.
"""

import math
import time
import numpy as np
from typing import Optional


class SyntheticAudioSource:
    """
    Generates synthetic audio blocks that simulate DJ track energy patterns.

    Designed to be a drop-in replacement for AudioCapture in test scripts:
      capture = SyntheticAudioSource(block_size=1024, sample_rate=44100)
      capture.start()
      block = capture.get_latest_block()   # returns ndarray or None
      capture.stop()

    Also exposes:
      .device_name   → "Synthetic (demo mode)"
      .last_error    → None
      .is_running()  → bool
    """

    def __init__(
        self,
        sample_rate: int = 44100,
        block_size: int = 1024,
    ):
        self.sample_rate = sample_rate
        self.block_size  = block_size

        self._running    = False
        self._phase      = 0.0        # continuous sample counter
        self._start_time: Optional[float] = None

    def start(self) -> None:
        self._running    = True
        self._phase      = 0.0
        self._start_time = time.monotonic()

    def stop(self) -> None:
        self._running = False

    def is_running(self) -> bool:
        return self._running

    @property
    def device_name(self) -> str:
        return "Synthetic (demo mode)"

    @property
    def last_error(self) -> Optional[str]:
        return None

    def get_latest_block(self) -> Optional[np.ndarray]:
        """Generate and return one synthetic audio block."""
        if not self._running:
            return None

        t_abs = time.monotonic() - self._start_time

        n     = self.block_size
        sr    = self.sample_rate
        t_rel = self._phase / sr   # time at start of this block (seconds)

        # Sample times for this block
        samples = np.arange(n, dtype=np.float32) / sr + t_rel

        # --- Envelope LFOs (slow amplitude modulation) ---
        # Bass level: rises and falls on a ~4s cycle
        bass_env  = 0.45 + 0.45 * np.sin(2 * math.pi * 0.25 * samples)
        # Mid level: ~3s cycle, offset phase
        mid_env   = 0.35 + 0.35 * np.sin(2 * math.pi * 0.33 * samples + 1.2)
        # High level: faster, ~1.5s
        high_env  = 0.20 + 0.20 * np.sin(2 * math.pi * 0.67 * samples + 2.5)

        # --- Bass sine (80 Hz kick fundamental) ---
        bass = bass_env * np.sin(2 * math.pi * 80.0 * samples).astype(np.float32)

        # --- Kick transient pulses: sharp attack every ~0.5s ---
        # Beat at 120 BPM → 0.5s per beat
        beat_phase = (samples * 2.0) % 1.0   # 0→1 per beat (120BPM)
        kick_env   = np.exp(-beat_phase * 18.0).astype(np.float32)  # fast decay
        kick       = kick_env * 0.7 * np.sin(2 * math.pi * 55.0 * samples).astype(np.float32)

        # --- Mid sine (800 Hz) ---
        mid = (mid_env * 0.5 * np.sin(2 * math.pi * 800.0 * samples)).astype(np.float32)

        # --- High: 6 kHz burst riding noise ---
        rng       = np.random.default_rng(int(self._phase) // n)
        noise     = rng.standard_normal(n).astype(np.float32) * 0.15
        hi_sine   = (high_env * 0.3 * np.sin(2 * math.pi * 6000.0 * samples)).astype(np.float32)
        highs     = hi_sine + noise * high_env.astype(np.float32)

        # --- Mix ---
        block = bass + kick + mid + highs

        # Soft clip to prevent overflow (tanh limiter)
        block = np.tanh(block).astype(np.float32)

        # Advance phase counter
        self._phase += n

        # Return shaped as (N, 1) to match AudioCapture output
        return block.reshape(-1, 1)
