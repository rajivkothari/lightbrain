"""
FFT-based audio analyzer.

Splits audio into frequency bands and normalizes them to 0.0–1.0 values
suitable for use as lighting control signals.

Bands:
  lows      20–150 Hz     — kick / bass fundamental
  low_mids  150–500 Hz    — bass upper harmonics, cello, bass guitar
  mids      500–2500 Hz   — vocals, snare, most instruments
  highs     2500–12000 Hz — cymbals, hi-hats, presence

MVP exposes: low_energy, mid_energy, high_energy, overall_energy
(low_mids are merged into mid_energy for Sprint 1)
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class AudioBands:
    low_energy:     float = 0.0
    mid_energy:     float = 0.0
    high_energy:    float = 0.0
    overall_energy: float = 0.0

    def as_dict(self) -> dict:
        return {
            "low_energy":     self.low_energy,
            "mid_energy":     self.mid_energy,
            "high_energy":    self.high_energy,
            "overall_energy": self.overall_energy,
        }


class AudioAnalyzer:
    """
    FFT-based band energy analyzer.

    Feed raw audio blocks via analyze() and get normalized 0.0–1.0 band
    energies back. Includes a running RMS-based gain normalization to handle
    different input levels without requiring manual gain staging.
    """

    # Band edges in Hz
    BAND_EDGES = {
        "lows":      (20,   150),
        "low_mids":  (150,  500),
        "mids":      (500,  2500),
        "highs":     (2500, 12000),
    }

    # Gain normalizer — tracks a running peak and slowly decays it
    GAIN_DECAY  = 0.9995   # per-frame decay of the peak tracking
    GAIN_FLOOR  = 1e-6     # minimum denominator to avoid divide-by-zero

    def __init__(self, sample_rate: int = 44100, block_size: int = 1024):
        self.sample_rate = sample_rate
        self.block_size  = block_size

        # Running peak for gain normalization (per-band)
        self._peak_low:     float = self.GAIN_FLOOR
        self._peak_mid:     float = self.GAIN_FLOOR
        self._peak_high:    float = self.GAIN_FLOOR
        self._peak_overall: float = self.GAIN_FLOOR

        # Precompute FFT frequency bins
        self._freqs = np.fft.rfftfreq(block_size, d=1.0 / sample_rate)

    def _band_energy(self, magnitudes: np.ndarray, freqs: np.ndarray,
                     f_low: float, f_high: float) -> float:
        """RMS energy in a frequency band."""
        mask = (freqs >= f_low) & (freqs < f_high)
        if not np.any(mask):
            return 0.0
        band = magnitudes[mask]
        return float(np.sqrt(np.mean(band ** 2)))

    def analyze(self, block: np.ndarray) -> AudioBands:
        """
        Analyze one audio block and return normalized band energies.

        block — 1D float32 array of audio samples (mono)
        Returns AudioBands with all values in 0.0–1.0.
        """
        if block is None or len(block) == 0:
            return AudioBands()

        # Flatten to mono if multi-channel
        if block.ndim > 1:
            block = block.mean(axis=1)

        # Skip blocks that don't match expected size — avoids mismatched freqs
        if len(block) != self.block_size:
            return AudioBands()

        # Apply Hann window to reduce spectral leakage
        windowed = block * np.hanning(self.block_size)

        # FFT — use magnitude (not power) for more musical response
        spectrum = np.abs(np.fft.rfft(windowed))

        # rfftfreq and rfft always produce block_size//2+1 elements
        # for the same block_size, so lengths always match.
        freqs = self._freqs

        low_e  = self._band_energy(spectrum, freqs, *self.BAND_EDGES["lows"])
        lmid_e = self._band_energy(spectrum, freqs, *self.BAND_EDGES["low_mids"])
        mid_e  = self._band_energy(spectrum, freqs, *self.BAND_EDGES["mids"])
        high_e = self._band_energy(spectrum, freqs, *self.BAND_EDGES["highs"])

        # Merge low_mids into mids for Sprint 1 (weighted blend)
        combined_mid = lmid_e * 0.4 + mid_e * 0.6

        # Overall = RMS of the whole block (time domain)
        overall = float(np.sqrt(np.mean(block ** 2)))

        # Running gain normalization — track per-band peak with slow decay
        self._peak_low     = max(self._peak_low     * self.GAIN_DECAY, low_e,      self.GAIN_FLOOR)
        self._peak_mid     = max(self._peak_mid     * self.GAIN_DECAY, combined_mid, self.GAIN_FLOOR)
        self._peak_high    = max(self._peak_high    * self.GAIN_DECAY, high_e,     self.GAIN_FLOOR)
        self._peak_overall = max(self._peak_overall * self.GAIN_DECAY, overall,    self.GAIN_FLOOR)

        # Normalize and clamp — never let values exceed 1.0
        return AudioBands(
            low_energy=     min(1.0, low_e      / self._peak_low),
            mid_energy=     min(1.0, combined_mid / self._peak_mid),
            high_energy=    min(1.0, high_e     / self._peak_high),
            overall_energy= min(1.0, overall    / self._peak_overall),
        )

    def reset_gain(self) -> None:
        """Reset gain normalization state (e.g. on mode switch)."""
        self._peak_low     = self.GAIN_FLOOR
        self._peak_mid     = self.GAIN_FLOOR
        self._peak_high    = self.GAIN_FLOOR
        self._peak_overall = self.GAIN_FLOOR
