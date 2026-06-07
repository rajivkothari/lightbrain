"""
Offline audio analyzer for Song Preview Mode.

Batch-processes a full audio array using the same FFT window/hop as the live
analyzer, producing an AnalysisTimeline with:
  - Per-frame band energies (low/mid/high/overall)
  - Onset markers via spectral flux
  - Rough phrase/drop detection via energy variance
  - BPM estimate from onset intervals

The AnalysisTimeline is the input to DeterministicEngine (engine/deterministic.py).
Same file + same settings → identical AnalysisTimeline every run.

Exported types:
  AnalysisFrame, EventMarker, AnalysisTimeline, OfflineAnalyzer
"""

import math
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class AnalysisFrame:
    """Band energies + onset status for one audio block."""
    time_s:        float
    low_energy:    float
    mid_energy:    float
    high_energy:   float
    overall_energy: float
    is_onset:      bool
    onset_strength: float   # 0.0–1.0 normalized excess above baseline


@dataclass
class EventMarker:
    """A labelled event detected in the audio timeline."""
    time_s:     float
    label:      str         # "phrase_start" | "drop" | "breakdown" | "silence"
    confidence: float       # 0.0–1.0


@dataclass
class AnalysisTimeline:
    """Complete offline analysis of one audio file."""
    frames:       List[AnalysisFrame]
    events:       List[EventMarker]
    duration_s:   float
    sample_rate:  int
    hop_size:     int           # block size (frames advance one hop at a time)
    window_size:  int           # FFT window (same as hop in this implementation)
    bpm_estimate: Optional[float] = None
    key_estimate: Optional[str]  = None   # reserved for future key detection


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class OfflineAnalyzer:
    """
    Batch audio analyzer.

    analyze(audio, sample_rate) processes the full mono float32 array and
    returns an AnalysisTimeline with one frame per hop.

    Uses the same band definitions as AudioAnalyzer (audio/analyzer.py):
      lows:     20–150 Hz
      low_mids: 150–500 Hz  (merged into mid)
      mids:     500–2500 Hz
      highs:    2500–12000 Hz

    Onset detection: spectral flux (sum of positive half-wave-rectified
    spectral differences between consecutive frames), thresholded at
    flux_threshold × rolling mean.
    """

    # Same band edges as AudioAnalyzer
    BAND_EDGES = {
        "lows":      (20,   150),
        "low_mids":  (150,  500),
        "mids":      (500,  2500),
        "highs":     (2500, 12000),
    }

    # Gain normalization: running peak tracker
    GAIN_DECAY = 0.9995
    GAIN_FLOOR = 1e-6

    def __init__(
        self,
        block_size: int = 1024,
        onset_flux_threshold: float = 1.4,
        onset_history_size: int = 20,
        min_onset_interval_s: float = 0.15,
        phrase_window_s: float = 4.0,
        phrase_energy_threshold: float = 0.4,
    ):
        """
        block_size              — FFT window/hop size (matches live analyzer)
        onset_flux_threshold    — flux must exceed N × rolling mean to fire onset
        onset_history_size      — frames in rolling flux mean window
        min_onset_interval_s    — minimum seconds between onset events
        phrase_window_s         — window for phrase energy detection (seconds)
        phrase_energy_threshold — mean energy above this → phrase region
        """
        self.block_size              = block_size
        self.onset_flux_threshold    = onset_flux_threshold
        self.onset_history_size      = onset_history_size
        self.min_onset_interval_s    = min_onset_interval_s
        self.phrase_window_s         = phrase_window_s
        self.phrase_energy_threshold = phrase_energy_threshold

    def analyze(
        self,
        audio: np.ndarray,
        sample_rate: int = 44100,
    ) -> AnalysisTimeline:
        """
        Process the full mono float32 audio array.

        audio       — 1D float32 array, mono, any length
        sample_rate — audio sample rate in Hz (default 44100)
        Returns AnalysisTimeline with one frame per hop.
        """
        if audio is None or len(audio) == 0:
            return AnalysisTimeline(
                frames=[], events=[], duration_s=0.0,
                sample_rate=sample_rate,
                hop_size=self.block_size,
                window_size=self.block_size,
            )

        # Ensure mono float32
        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)

        hop      = self.block_size
        n_frames = max(0, (len(audio) - hop) // hop + 1)
        duration = len(audio) / sample_rate

        freqs = np.fft.rfftfreq(hop, d=1.0 / sample_rate)
        hann  = np.hanning(hop)

        # Running gain peaks
        peak_low  = self.GAIN_FLOOR
        peak_mid  = self.GAIN_FLOOR
        peak_high = self.GAIN_FLOOR
        peak_all  = self.GAIN_FLOOR

        frames: List[AnalysisFrame] = []
        prev_spectrum: Optional[np.ndarray] = None
        flux_history: List[float] = []

        # First pass: compute band energies + raw flux
        raw_frames = []      # (time_s, low, mid, high, overall, raw_flux)

        for i in range(n_frames):
            start  = i * hop
            block  = audio[start: start + hop]
            if len(block) < hop:
                block = np.pad(block, (0, hop - len(block)))

            time_s = start / sample_rate

            windowed = block * hann
            spectrum = np.abs(np.fft.rfft(windowed))

            low_e  = self._band_rms(spectrum, freqs, *self.BAND_EDGES["lows"])
            lmid_e = self._band_rms(spectrum, freqs, *self.BAND_EDGES["low_mids"])
            mid_e  = self._band_rms(spectrum, freqs, *self.BAND_EDGES["mids"])
            high_e = self._band_rms(spectrum, freqs, *self.BAND_EDGES["highs"])
            combined_mid = lmid_e * 0.4 + mid_e * 0.6
            overall = float(np.sqrt(np.mean(block ** 2)))

            # Spectral flux: sum of positive-only differences
            if prev_spectrum is not None:
                diff = spectrum - prev_spectrum
                flux = float(np.sum(np.maximum(diff, 0.0)))
            else:
                flux = 0.0
            prev_spectrum = spectrum

            # Update gain peaks
            peak_low  = max(peak_low  * self.GAIN_DECAY, low_e,         self.GAIN_FLOOR)
            peak_mid  = max(peak_mid  * self.GAIN_DECAY, combined_mid,  self.GAIN_FLOOR)
            peak_high = max(peak_high * self.GAIN_DECAY, high_e,         self.GAIN_FLOOR)
            peak_all  = max(peak_all  * self.GAIN_DECAY, overall,        self.GAIN_FLOOR)

            raw_frames.append((
                time_s,
                min(1.0, low_e         / peak_low),
                min(1.0, combined_mid  / peak_mid),
                min(1.0, high_e        / peak_high),
                min(1.0, overall       / peak_all),
                flux,
            ))

        # Normalize flux to 0–1 for onset strength
        all_flux = [f[5] for f in raw_frames]
        flux_max = max(all_flux) if all_flux else 1.0
        if flux_max < self.GAIN_FLOOR:
            flux_max = 1.0

        # Second pass: onset detection + build AnalysisFrames
        last_onset_time = -999.0
        for i, (time_s, low, mid, high, overall, raw_flux) in enumerate(raw_frames):
            # Rolling mean of flux history
            flux_history.append(raw_flux)
            if len(flux_history) > self.onset_history_size:
                flux_history.pop(0)
            flux_mean = sum(flux_history) / len(flux_history) if flux_history else 0.0

            is_onset = False
            onset_strength = 0.0
            interval_ok = (time_s - last_onset_time) >= self.min_onset_interval_s

            if (flux_mean > self.GAIN_FLOOR
                    and raw_flux > self.onset_flux_threshold * flux_mean
                    and interval_ok):
                is_onset = True
                onset_strength = min(1.0, raw_flux / (flux_max + self.GAIN_FLOOR))
                last_onset_time = time_s

            frames.append(AnalysisFrame(
                time_s=time_s,
                low_energy=low,
                mid_energy=mid,
                high_energy=high,
                overall_energy=overall,
                is_onset=is_onset,
                onset_strength=onset_strength,
            ))

        # Build event markers from phrase detection
        events = self._detect_events(frames, sample_rate)

        # BPM estimate from onset intervals
        bpm = self._estimate_bpm(frames)

        return AnalysisTimeline(
            frames=frames,
            events=events,
            duration_s=duration,
            sample_rate=sample_rate,
            hop_size=hop,
            window_size=hop,
            bpm_estimate=bpm,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _band_rms(spectrum: np.ndarray, freqs: np.ndarray,
                  f_low: float, f_high: float) -> float:
        mask = (freqs >= f_low) & (freqs < f_high)
        if not np.any(mask):
            return 0.0
        band = spectrum[mask]
        return float(np.sqrt(np.mean(band ** 2)))

    def _detect_events(
        self,
        frames: List[AnalysisFrame],
        sample_rate: int,
    ) -> List[EventMarker]:
        """Simple phrase/drop detection from energy curve."""
        events: List[EventMarker] = []
        if not frames:
            return events

        hop       = self.block_size
        win_frames = max(1, int(self.phrase_window_s * sample_rate / hop))

        energies = np.array([f.overall_energy for f in frames], dtype=np.float32)
        n        = len(energies)

        in_phrase = False
        for i in range(0, n, win_frames // 2):
            window = energies[i: i + win_frames]
            mean_e = float(np.mean(window)) if len(window) > 0 else 0.0

            if mean_e >= self.phrase_energy_threshold and not in_phrase:
                events.append(EventMarker(
                    time_s=frames[i].time_s,
                    label="phrase_start",
                    confidence=min(1.0, mean_e),
                ))
                in_phrase = True
            elif mean_e < self.phrase_energy_threshold * 0.6 and in_phrase:
                in_phrase = False

        # Drop detection: sudden large energy increase after a quieter section
        for i in range(win_frames, n - win_frames):
            before = float(np.mean(energies[max(0, i - win_frames): i]))
            after  = float(np.mean(energies[i: i + win_frames // 4]))
            if after > 1.8 * before and after > 0.6:
                events.append(EventMarker(
                    time_s=frames[i].time_s,
                    label="drop",
                    confidence=min(1.0, after - before),
                ))
                i += win_frames  # skip ahead to avoid duplicate drops

        events.sort(key=lambda e: e.time_s)
        return events

    @staticmethod
    def _estimate_bpm(frames: List[AnalysisFrame]) -> Optional[float]:
        """Estimate BPM from inter-onset intervals (simple approach)."""
        onset_times = [f.time_s for f in frames if f.is_onset]
        if len(onset_times) < 4:
            return None
        intervals = [onset_times[i + 1] - onset_times[i]
                     for i in range(len(onset_times) - 1)]
        # Filter to plausible beat range: 60–200 BPM → 0.3–1.0s intervals
        valid = [iv for iv in intervals if 0.28 <= iv <= 1.05]
        if len(valid) < 3:
            return None
        mean_iv = sum(valid) / len(valid)
        bpm = 60.0 / mean_iv
        # Bring into 60–180 BPM range by halving/doubling
        while bpm > 180:
            bpm /= 2
        while bpm < 60:
            bpm *= 2
        return round(bpm, 1)
