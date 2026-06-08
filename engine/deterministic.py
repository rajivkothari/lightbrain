"""
DeterministicEngine — drives the LightBrain pipeline from an AnalysisTimeline
with a fixed clock and seeded RNG to produce a fully deterministic
FixtureStateTimeline.

Guarantee:
  same AnalysisTimeline + same SettingsSnapshot + same seed
  → identical FixtureStateTimeline every run

Clock injection:
  All time-dependent engine components (LaneSmoother, PaletteBlender,
  SceneLayout) are driven with frame.time_s instead of time.monotonic(),
  making beam animation, palette transitions, and envelope following
  100% reproducible.

Usage:
  from engine.deterministic import DeterministicEngine
  from engine.settings_snapshot import SettingsSnapshot

  settings = SettingsSnapshot(mode_key="banger", palette_key="banger")
  engine   = DeterministicEngine(settings, seed=42)
  timeline = engine.generate(analysis_timeline, all_palettes)
"""

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from audio.beat_detector   import BeatDetector
from audio.offline_analyzer import AnalysisTimeline

from engine.settings_snapshot import SettingsSnapshot
from engine.smoothing          import LaneSmoother
from engine.lanes              import RoomLane
from engine.modes              import get_mode
from engine.safety             import SafetyEngine

from app.render.scene         import SceneLayout
from app.render.fixture_state import FixtureStateTimeline, TimedFrame


class DeterministicEngine:
    """
    Offline deterministic lighting engine.

    Processes each frame of an AnalysisTimeline in sequence, passing
    frame.time_s as the clock source to all time-dependent components.
    """

    def __init__(self, settings: SettingsSnapshot, seed: int = 42):
        """
        settings — mode/palette/override snapshot
        seed     — RNG seed for reproducible randomness (used by numpy RNG
                   in any future randomized effects)
        """
        self._settings = settings
        self._seed     = seed

    def generate(
        self,
        timeline: AnalysisTimeline,
        palettes: dict,
    ) -> FixtureStateTimeline:
        """
        Process the full AnalysisTimeline and return a FixtureStateTimeline.

        timeline — AnalysisTimeline from OfflineAnalyzer
        palettes — dict of palette_key → Palette (from load_all_palettes)
        """
        frames = timeline.frames
        if not frames:
            return FixtureStateTimeline(
                duration_s=timeline.duration_s,
                random_seed=self._seed,
                settings_key=self._settings.mode_key,
            )

        hop_s = timeline.hop_size / max(timeline.sample_rate, 1)
        # Initialize clocks to one hop before the first frame so the first
        # update() computes dt = hop_s (a realistic one-frame advance)
        t0 = frames[0].time_s - hop_s

        # --- Fresh engine instances (no live state contamination) ---
        mode    = get_mode(self._settings.mode_key)
        palette = palettes.get(
            self._settings.palette_key,
            palettes.get(mode.palette_key, next(iter(palettes.values())))
        )

        # Apply settings overrides if provided
        if self._settings.base_brightness_override is not None:
            mode = _override(mode, "base_brightness", self._settings.base_brightness_override)
        if self._settings.max_brightness_override is not None:
            mode = _override(mode, "max_brightness", self._settings.max_brightness_override)
        if self._settings.pulse_amount_override is not None:
            mode = _override(mode, "pulse_amount", self._settings.pulse_amount_override)
        if self._settings.saturation_scale_override is not None:
            mode = _override(mode, "saturation_scale", self._settings.saturation_scale_override)

        smoother = LaneSmoother(self._settings.mode_key)
        safety   = SafetyEngine()
        safety.update_from_mode(mode)
        room_lane = RoomLane(palette, mode=mode)
        scene     = SceneLayout()

        # Initialize all clocks to t0
        smoother.reset_all(now=t0)
        room_lane.reset_time(t0)
        scene.reset_time(t0)

        # Seed numpy RNG for any randomized rendering effects
        _rng = np.random.default_rng(self._seed)

        result_frames: list = []

        for frame in frames:
            now   = frame.time_s
            bands = {
                "low_energy":     frame.low_energy,
                "mid_energy":     frame.mid_energy,
                "high_energy":    frame.high_energy,
                "overall_energy": frame.overall_energy,
            }

            lanes = smoother.update(bands, now=now)

            room_out = room_lane.render(
                smoothed_room=lanes["room"],
                impact=lanes["impact"],
                safety=safety,
                master_dimmer=self._settings.master_dimmer,
                beat_trigger=frame.is_onset,
                now=now,
            )

            rig_state = scene.update_and_build(
                bands=bands,
                lanes=lanes,
                hue=room_out.hsv.h,
                saturation=room_out.hsv.s,
                brightness=room_out.brightness,
                base_brt=room_out.base_brightness,
                pulse_brt=room_out.pulse_brightness,
                mode_key=mode.key,
                palette_name=room_lane.palette_name,
                blackout=False,
                now=now,
            )

            result_frames.append(TimedFrame(time_s=now, state=rig_state))

        fps = timeline.sample_rate / timeline.hop_size
        return FixtureStateTimeline(
            frames=result_frames,
            duration_s=timeline.duration_s,
            sample_rate_fps=fps,
            random_seed=self._seed,
            settings_key=self._settings.mode_key,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _override(mode, field: str, value: float):
    """Return a copy of mode with field set to value (dataclass replace)."""
    from dataclasses import replace
    return replace(mode, **{field: value})
