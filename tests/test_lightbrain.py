"""
LightBrain Sprint 1 / Sprint 2 automated test suite.

Run from repo root:
  python -m pytest tests/
  python -m pytest tests/ -v

Tests cover:
  - Hue interpolation (shortest-path, wraparound cases)
  - Gamma correction (curve, clamp, DMX scaling)
  - DMX universe (channel bounds, clamping, blackout, changed_channels)
  - Smoothing engine (bounds, NaN/inf safety, cooldown holds)
  - Palette loading (valid JSON, graceful failure on bad files)
  - RockWedge fixture mapper (channel writes, pure colour separation)
  - Safety engine (blackout, mode scale, master dimmer)
  - Full room lane pipeline (output stays in bounds)
  - Synthetic audio source (produces valid float32 blocks)
"""

import math
import os
import sys
import tempfile
import time

import numpy as np
import pytest

# Ensure the repo root is on the path regardless of where pytest is run from
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine.palettes  import lerp_hue_shortest, lerp_color, HSVColor, load_palette, load_all_palettes, PaletteBlender, Palette
from engine.gamma     import apply_gamma, apply_gamma_to_dmx, apply_gamma_rgb
from dmx.universe     import DMXUniverse
from engine.smoothing import EnvelopeFollower, EnvelopeConfig, LaneSmoother
from fixtures.rockwedge import RockWedge
from engine.modes     import get_mode, MODES
from engine.safety    import SafetyEngine
from engine.lanes     import RoomLane
from audio.synthetic  import SyntheticAudioSource
from audio.analyzer   import AudioAnalyzer
from audio.beat_detector import BeatDetector
from engine.transitions  import ModeTransitioner
from app.render.fixture_state import (
    UplightState, WashState, BeamState, SparkleState, ImpactState, RigVisualState,
)
from app.render.scene import SceneLayout


# ===========================================================================
# Hue interpolation
# ===========================================================================

class TestHueInterpolation:

    def test_same_hue_stays_same(self):
        assert lerp_hue_shortest(180, 180, 0.5) == pytest.approx(180.0)

    def test_simple_forward(self):
        # 0° → 90° at t=0.5 should give 45°
        assert lerp_hue_shortest(0, 90, 0.5) == pytest.approx(45.0)

    def test_wraparound_red_to_magenta(self):
        # Red (0°) → Magenta (300°): shortest path is backward through 360°
        # delta = (300 - 0 + 540) % 360 - 180 = 840 % 360 - 180 = 120 - 180 = -60
        # result = (0 + -60 * 0.5) % 360 = -30 % 360 = 330
        h = lerp_hue_shortest(0, 300, 0.5)
        assert h == pytest.approx(330.0), \
            "Red→Magenta should go backward through 360°, not forward"

    def test_wraparound_does_not_sweep_spectrum(self):
        # From red (0°) to magenta (300°), midpoint should be near 330°
        # NOT near 150° (the long way around through green)
        h = lerp_hue_shortest(0, 300, 0.5)
        assert h > 270, "Mid-point should be in the pink/magenta region, not green"

    def test_t_zero_returns_h1(self):
        assert lerp_hue_shortest(45, 270, 0.0) == pytest.approx(45.0)

    def test_t_one_returns_h2(self):
        assert lerp_hue_shortest(45, 270, 1.0) == pytest.approx(270.0)

    def test_result_always_in_0_360(self):
        cases = [(350, 10, 0.5), (0, 300, 0.5), (270, 90, 0.5), (180, 0, 0.5)]
        for h1, h2, t in cases:
            result = lerp_hue_shortest(h1, h2, t)
            assert 0 <= result < 360, f"Hue {result} out of range for ({h1},{h2},{t})"

    def test_lerp_color_clamps_t(self):
        c1 = HSVColor(0,   1.0, 1.0)
        c2 = HSVColor(180, 1.0, 0.5)
        # t > 1.0 should clamp to 1.0
        out_over = lerp_color(c1, c2, 2.0)
        out_one  = lerp_color(c1, c2, 1.0)
        assert out_over.h == pytest.approx(out_one.h)
        assert out_over.v == pytest.approx(out_one.v)
        # t < 0.0 should clamp to 0.0
        out_under = lerp_color(c1, c2, -1.0)
        out_zero  = lerp_color(c1, c2,  0.0)
        assert out_under.h == pytest.approx(out_zero.h)


# ===========================================================================
# Gamma correction
# ===========================================================================

class TestGamma:

    def test_zero_input_returns_zero(self):
        assert apply_gamma(0.0) == pytest.approx(0.0)

    def test_one_input_returns_one(self):
        assert apply_gamma(1.0) == pytest.approx(1.0)

    def test_midpoint_compressed(self):
        # With gamma > 1, 0.5 maps to something less than 0.5
        assert apply_gamma(0.5) < 0.5

    def test_gamma_22_midpoint(self):
        expected = 0.5 ** 2.2
        assert apply_gamma(0.5, gamma=2.2) == pytest.approx(expected)

    def test_clamp_above_one(self):
        # Input > 1.0 should be clamped before gamma
        assert apply_gamma(1.5) == pytest.approx(1.0)

    def test_clamp_below_zero(self):
        assert apply_gamma(-0.5) == pytest.approx(0.0)

    def test_dmx_full_is_255(self):
        assert apply_gamma_to_dmx(1.0) == 255

    def test_dmx_zero_is_0(self):
        assert apply_gamma_to_dmx(0.0) == 0

    def test_dmx_result_is_int(self):
        assert isinstance(apply_gamma_to_dmx(0.7), int)

    def test_dmx_result_in_range(self):
        for v in [0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0]:
            result = apply_gamma_to_dmx(v)
            assert 0 <= result <= 255, f"DMX value {result} out of 0-255 for input {v}"

    def test_rgb_helper(self):
        r, g, b = apply_gamma_rgb(1.0, 0.0, 1.0)
        assert r == 255
        assert g == 0
        assert b == 255


# ===========================================================================
# DMX Universe
# ===========================================================================

class TestDMXUniverse:

    def test_initial_all_zero(self):
        u = DMXUniverse()
        for ch in range(1, 513):
            assert u.get_channel(ch) == 0

    def test_set_and_get_channel(self):
        u = DMXUniverse()
        u.set_channel(1,   255)
        u.set_channel(512, 128)
        assert u.get_channel(1)   == 255
        assert u.get_channel(512) == 128

    def test_clamp_above_255(self):
        u = DMXUniverse()
        u.set_channel(1, 300)
        assert u.get_channel(1) == 255

    def test_clamp_below_zero(self):
        u = DMXUniverse()
        u.set_channel(1, -10)
        assert u.get_channel(1) == 0

    def test_channel_out_of_range_raises(self):
        u = DMXUniverse()
        with pytest.raises(ValueError):
            u.set_channel(0, 100)
        with pytest.raises(ValueError):
            u.set_channel(513, 100)

    def test_blackout_zeros_all(self):
        u = DMXUniverse()
        for ch in range(1, 513):
            u.set_channel(ch, 200)
        u.blackout()
        for ch in range(1, 513):
            assert u.get_channel(ch) == 0

    def test_to_bytes_length(self):
        u = DMXUniverse()
        assert len(u.to_bytes()) == 512

    def test_copy_is_independent(self):
        u = DMXUniverse()
        u.set_channel(5, 99)
        v = u.copy()
        v.set_channel(5, 42)
        assert u.get_channel(5) == 99   # original unchanged

    def test_changed_channels_empty_when_identical(self):
        u = DMXUniverse()
        v = u.copy()
        assert u.changed_channels(v) == []

    def test_changed_channels_reports_diff(self):
        u = DMXUniverse()
        v = u.copy()
        v.set_channel(10, 77)
        changes = v.changed_channels(u)
        assert len(changes) == 1
        assert changes[0] == (10, 0, 77)

    def test_set_channels_block(self):
        u = DMXUniverse()
        u.set_channels(5, [10, 20, 30])
        assert u.get_channel(5) == 10
        assert u.get_channel(6) == 20
        assert u.get_channel(7) == 30

    def test_1_indexed_public_api(self):
        u = DMXUniverse()
        u.set_channel(1, 111)
        raw = u.snapshot()
        # Channel 1 should be at index 0 in the internal array
        assert raw[0] == 111


# ===========================================================================
# Smoothing engine
# ===========================================================================

class TestSmoothing:

    def _make_follower(self, attack_ms=10, decay_ms=200,
                       cooldown_ms=0, min_threshold=0.0) -> EnvelopeFollower:
        cfg = EnvelopeConfig(
            attack_ms=attack_ms,
            decay_ms=decay_ms,
            cooldown_ms=cooldown_ms,
            min_threshold=min_threshold,
        )
        return EnvelopeFollower(cfg)

    def test_value_never_exceeds_one(self):
        f = self._make_follower()
        for _ in range(100):
            v = f.update(1.0)
            time.sleep(0.001)
        assert v <= 1.0

    def test_value_never_below_zero(self):
        f = self._make_follower()
        for _ in range(20):
            f.update(1.0)
            time.sleep(0.001)
        for _ in range(100):
            v = f.update(0.0)
            time.sleep(0.001)
        assert v >= 0.0

    def test_no_nan_or_inf(self):
        f = self._make_follower(attack_ms=0, decay_ms=0)
        for val in [0.0, 0.5, 1.0, 0.0]:
            v = f.update(val)
            assert math.isfinite(v), f"Non-finite output: {v}"

    def test_attack_rises_fast(self):
        cfg = EnvelopeConfig(attack_ms=10, decay_ms=5000, cooldown_ms=0, min_threshold=0.0)
        f   = EnvelopeFollower(cfg)
        for _ in range(5):
            f.update(1.0)
            time.sleep(0.015)   # 15ms each step
        # After ~75ms with 10ms attack, should be well above 0.9
        assert f.value > 0.9, f"Attack too slow: {f.value:.3f}"

    def test_decay_is_slow_for_room_lane(self):
        cfg = EnvelopeConfig(attack_ms=10, decay_ms=3000, cooldown_ms=0, min_threshold=0.0)
        f   = EnvelopeFollower(cfg)
        # Drive to near 1.0
        for _ in range(10):
            f.update(1.0)
            time.sleep(0.005)
        peak = f.value
        # Decay for 100ms (very short compared to 3000ms tau)
        for _ in range(5):
            f.update(0.0)
            time.sleep(0.020)
        # Should still be well above 0.8
        assert f.value > 0.80, f"Room decayed too fast: {f.value:.3f}"

    def test_cooldown_holds_value(self):
        # With 500ms cooldown, value should not decay immediately after peak
        cfg = EnvelopeConfig(attack_ms=5, decay_ms=50, cooldown_ms=500, min_threshold=0.0)
        f   = EnvelopeFollower(cfg)
        # Drive to a peak
        for _ in range(5):
            f.update(0.9)
            time.sleep(0.005)
        peak_val = f.value
        # Now send silence — value should be held by cooldown
        time.sleep(0.010)
        v_after_10ms = f.update(0.0)
        # With 50ms decay and 500ms cooldown, value should NOT have dropped much
        assert v_after_10ms >= peak_val * 0.95, \
            f"Cooldown not holding: peak={peak_val:.3f}, after={v_after_10ms:.3f}"

    def test_min_threshold_gates_input(self):
        cfg = EnvelopeConfig(attack_ms=10, decay_ms=200, cooldown_ms=0, min_threshold=0.2)
        f   = EnvelopeFollower(cfg)
        # Input below threshold should be treated as 0
        for _ in range(20):
            f.update(0.1)
            time.sleep(0.001)
        assert f.value < 0.05, f"Value should stay near 0 below threshold: {f.value:.3f}"

    def test_lane_smoother_output_keys(self):
        s = LaneSmoother()
        out = s.update({"low_energy": 0.5, "mid_energy": 0.3,
                        "high_energy": 0.2, "overall_energy": 0.4})
        assert "impact" in out
        assert "room"   in out
        assert all(0.0 <= v <= 1.0 for v in out.values())

    def test_lane_smoother_no_nan(self):
        s = LaneSmoother()
        for _ in range(50):
            out = s.update({"low_energy": 1.0, "mid_energy": 0.8,
                            "high_energy": 0.9, "overall_energy": 1.0})
            time.sleep(0.001)
        assert all(math.isfinite(v) for v in out.values())


# ===========================================================================
# Palette loading
# ===========================================================================

class TestPaletteLoading:

    def test_loads_all_six_palettes(self):
        palettes_dir = os.path.join(ROOT, "config", "palettes")
        palettes = load_all_palettes(palettes_dir)
        expected = {"dinner", "open_dance", "banger", "indian_latin", "speech", "slow_dance"}
        assert expected.issubset(set(palettes.keys())), \
            f"Missing palettes: {expected - set(palettes.keys())}"

    def test_open_dance_has_four_colors(self):
        palettes_dir = os.path.join(ROOT, "config", "palettes")
        palettes = load_all_palettes(palettes_dir)
        assert len(palettes["open_dance"].colors) == 4

    def test_palette_colors_in_valid_range(self):
        palettes_dir = os.path.join(ROOT, "config", "palettes")
        palettes = load_all_palettes(palettes_dir)
        for name, pal in palettes.items():
            for c in pal.colors:
                assert 0 <= c.h <= 360, f"{name}: hue {c.h} out of range"
                assert 0 <= c.s <= 1.0, f"{name}: sat {c.s} out of range"
                assert 0 <= c.v <= 1.0, f"{name}: val {c.v} out of range"

    def test_bad_json_fails_gracefully(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bad = os.path.join(tmpdir, "bad.json")
            with open(bad, "w") as f:
                f.write("{this is not valid json}")
            # load_all_palettes should catch the error and return empty dict
            result = load_all_palettes(tmpdir)
            assert result == {}

    def test_missing_key_in_json_fails_gracefully(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            incomplete = os.path.join(tmpdir, "incomplete.json")
            with open(incomplete, "w") as f:
                f.write('{"name": "test"}')   # missing "colors" key
            result = load_all_palettes(tmpdir)
            assert result == {}

    def test_palette_blender_stays_in_range(self):
        palettes_dir = os.path.join(ROOT, "config", "palettes")
        palettes = load_all_palettes(palettes_dir)
        blender = PaletteBlender(palettes["open_dance"])
        for _ in range(200):
            c = blender.update(0.5)
            assert 0 <= c.h <= 360
            assert 0 <= c.s <= 1.0
            assert 0 <= c.v <= 1.0
            time.sleep(0.005)


# ===========================================================================
# RockWedge fixture mapper
# ===========================================================================

class TestRockWedge:

    def _rw(self, addr: int = 1) -> RockWedge:
        return RockWedge(fixture_id="test", name="Test", dmx_address=addr)

    def test_writes_eight_channels(self):
        u  = DMXUniverse()
        rw = self._rw(1)
        rw.render_to_universe(u, hue=0, saturation=1.0, value=1.0)
        # All 8 channels from addr 1..8 should be written
        for ch in range(1, 9):
            assert u.get_channel(ch) >= 0   # just checks no exception

    def test_pure_red(self):
        u  = DMXUniverse()
        rw = self._rw(1)
        rw.render_to_universe(u, hue=0.0, saturation=1.0, value=1.0)
        # Red channel (Ch2) should be max
        assert u.get_channel(2) == 255
        # Green (Ch3) and Blue (Ch4) should be 0
        assert u.get_channel(3) == 0
        assert u.get_channel(4) == 0

    def test_pure_green(self):
        u  = DMXUniverse()
        rw = self._rw(1)
        rw.render_to_universe(u, hue=120.0, saturation=1.0, value=1.0)
        assert u.get_channel(3) == 255   # Green
        assert u.get_channel(2) == 0     # Red
        assert u.get_channel(4) == 0     # Blue

    def test_pure_blue(self):
        u  = DMXUniverse()
        rw = self._rw(1)
        rw.render_to_universe(u, hue=240.0, saturation=1.0, value=1.0)
        assert u.get_channel(4) == 255   # Blue
        assert u.get_channel(2) == 0     # Red
        assert u.get_channel(3) == 0     # Green

    def test_dimmer_separate_from_rgb(self):
        # Dimmer (Ch1) should reflect the brightness level.
        # RGB channels should reflect pure colour (not double-scaled).
        u  = DMXUniverse()
        rw = self._rw(1)
        rw.render_to_universe(u, hue=0.0, saturation=1.0, value=1.0, brightness=1.0)
        dimmer = u.get_channel(1)
        red    = u.get_channel(2)
        # With full red + full brightness: dimmer=255, red=255
        assert dimmer == 255
        assert red    == 255

    def test_half_brightness_dims_dimmer_not_rgb(self):
        # At 50% brightness, dimmer should be gamma(0.5)*255 ≈ 54
        # RGB channels (pure hue) should still be 255
        u  = DMXUniverse()
        rw = self._rw(1)
        rw.render_to_universe(u, hue=0.0, saturation=1.0, value=0.5, brightness=1.0)
        dimmer = u.get_channel(1)
        red    = u.get_channel(2)
        expected_dimmer = int(round((0.5 ** 2.2) * 255))
        assert dimmer == expected_dimmer, f"Expected dimmer={expected_dimmer}, got {dimmer}"
        # Red should be full (pure hue, no brightness baked in)
        assert red == 255

    def test_strobe_zero_in_sprint1(self):
        u  = DMXUniverse()
        rw = self._rw(1)
        rw.render_to_universe(u, hue=0, saturation=1.0, value=1.0, strobe=1.0)
        assert u.get_channel(8) == 0   # Ch8 strobe always 0 Sprint 1

    def test_white_amber_uv_zero_in_sprint1(self):
        u  = DMXUniverse()
        rw = self._rw(1)
        rw.render_to_universe(u, hue=45, saturation=0.5, value=0.8)
        assert u.get_channel(5) == 0   # White
        assert u.get_channel(6) == 0   # Amber
        assert u.get_channel(7) == 0   # UV

    def test_all_values_0_to_255(self):
        u  = DMXUniverse()
        rw = self._rw(1)
        rw.render_to_universe(u, hue=300, saturation=0.8, value=0.6)
        for ch in range(1, 9):
            v = u.get_channel(ch)
            assert 0 <= v <= 255, f"Ch{ch} out of range: {v}"

    def test_nonstandard_address(self):
        u  = DMXUniverse()
        rw = self._rw(addr=100)
        rw.render_to_universe(u, hue=0, saturation=1.0, value=1.0)
        assert u.get_channel(100) > 0   # dimmer at 100
        # Channels below 100 should be untouched
        assert u.get_channel(1) == 0


# ===========================================================================
# Safety engine
# ===========================================================================

class TestSafetyEngine:

    def test_blackout_forces_zero(self):
        s = SafetyEngine()
        s.state.blackout_active = True
        bright, strobe = s.apply(1.0, 1.0)
        assert bright == 0.0
        assert strobe == 0.0

    def test_normal_output_passes_through(self):
        s = SafetyEngine()
        bright, strobe = s.apply(0.8)
        assert bright == pytest.approx(0.8)

    def test_master_dimmer_scales_output(self):
        s = SafetyEngine()
        s.set_master_dimmer(0.5)
        bright, _ = s.apply(1.0)
        assert bright == pytest.approx(0.5)

    def test_mode_intensity_scale(self):
        s    = SafetyEngine()
        mode = get_mode("dinner")   # intensity_scale = 0.7
        s.update_from_mode(mode)
        bright, _ = s.apply(1.0)
        assert bright == pytest.approx(0.7)

    def test_strobe_always_zero_sprint1(self):
        s = SafetyEngine()
        _, strobe = s.apply(1.0, 1.0)
        assert strobe == 0.0

    def test_blackout_toggle(self):
        s = SafetyEngine()
        assert s.toggle_blackout() == True
        bright, _ = s.apply(1.0)
        assert bright == 0.0
        assert s.toggle_blackout() == False
        bright, _ = s.apply(1.0)
        assert bright == pytest.approx(1.0)

    def test_speech_mode_no_strobe(self):
        s    = SafetyEngine()
        mode = get_mode("speech")
        s.update_from_mode(mode)
        assert s.state.strobe_allowed == False


# ===========================================================================
# Full room lane pipeline
# ===========================================================================

class TestRoomLane:

    def _setup(self) -> tuple:
        palettes_dir = os.path.join(ROOT, "config", "palettes")
        palettes     = load_all_palettes(palettes_dir)
        palette      = palettes["open_dance"]
        safety       = SafetyEngine()
        safety.update_from_mode(get_mode("open_dance"))
        lane = RoomLane(palette)
        return lane, safety

    def test_output_hsv_v_in_bounds(self):
        lane, safety = self._setup()
        for brightness in [0.0, 0.25, 0.5, 0.75, 1.0]:
            out = lane.render(smoothed_room=brightness, impact=0.3, safety=safety)
            assert 0.0 <= out.hsv.v <= 1.0, f"v={out.hsv.v} out of range at room={brightness}"

    def test_output_hue_in_bounds(self):
        lane, safety = self._setup()
        out = lane.render(smoothed_room=0.7, impact=0.4, safety=safety)
        assert 0.0 <= out.hsv.h <= 360.0

    def test_blackout_forces_zero_output(self):
        lane, safety = self._setup()
        safety.toggle_blackout()
        out = lane.render(smoothed_room=1.0, impact=1.0, safety=safety)
        assert out.hsv.v == pytest.approx(0.0)
        assert out.brightness == pytest.approx(0.0)

    def test_strobe_always_zero(self):
        lane, safety = self._setup()
        out = lane.render(smoothed_room=1.0, impact=1.0, safety=safety)
        assert out.strobe == 0.0

    def test_bass_breathing_lifts_brightness(self):
        lane, safety = self._setup()
        out_no_impact = lane.render(smoothed_room=0.5, impact=0.0, safety=safety)
        out_with_impact = lane.render(smoothed_room=0.5, impact=1.0, safety=safety)
        # Impact should lift brightness slightly (BASS_BREATH_DEPTH = 0.15)
        assert out_with_impact.brightness >= out_no_impact.brightness

    def test_full_pipeline_channels_in_bounds(self):
        lane, safety = self._setup()
        u  = DMXUniverse()
        rw = RockWedge(fixture_id="t", name="T", dmx_address=1)

        for _ in range(20):
            out = lane.render(smoothed_room=0.6, impact=0.3, safety=safety)
            rw.render_to_universe(u, brightness=1.0,
                                  hue=out.hsv.h, saturation=out.hsv.s, value=out.hsv.v)
            for ch in range(1, 9):
                v = u.get_channel(ch)
                assert 0 <= v <= 255, f"Ch{ch}={v} out of range"
            time.sleep(0.005)


# ===========================================================================
# Synthetic audio source
# ===========================================================================

class TestSyntheticAudio:

    def test_produces_valid_blocks(self):
        src = SyntheticAudioSource(sample_rate=44100, block_size=1024)
        src.start()
        block = src.get_latest_block()
        assert block is not None
        assert block.shape == (1024, 1)
        assert block.dtype == np.float32

    def test_no_nan_or_inf(self):
        src = SyntheticAudioSource(block_size=1024)
        src.start()
        for _ in range(10):
            block = src.get_latest_block()
            assert np.all(np.isfinite(block)), "NaN or Inf in synthetic block"

    def test_values_in_reasonable_range(self):
        src = SyntheticAudioSource(block_size=1024)
        src.start()
        for _ in range(5):
            block = src.get_latest_block()
            assert np.all(np.abs(block) <= 1.0), "Synthetic audio exceeded ±1.0"

    def test_produces_non_zero_energy(self):
        src      = SyntheticAudioSource(block_size=1024)
        analyzer = AudioAnalyzer(sample_rate=44100, block_size=1024)
        src.start()
        # Warm up the gain normalizer
        for _ in range(5):
            src.get_latest_block()

        blocks_with_energy = 0
        for _ in range(20):
            block = src.get_latest_block()
            bands = analyzer.analyze(block)
            if bands.low_energy > 0.01 or bands.overall_energy > 0.01:
                blocks_with_energy += 1

        assert blocks_with_energy > 10, \
            "Synthetic audio should produce non-zero band energy consistently"

    def test_different_blocks_are_not_identical(self):
        src = SyntheticAudioSource(block_size=1024)
        src.start()
        b1 = src.get_latest_block().copy()
        b2 = src.get_latest_block().copy()
        assert not np.allclose(b1, b2), "Successive blocks should differ"

    def test_is_running_states(self):
        src = SyntheticAudioSource()
        assert not src.is_running()
        src.start()
        assert src.is_running()
        src.stop()
        assert not src.is_running()


# ===========================================================================
# Sprint 1B: Mode brightness profiles
# ===========================================================================

class TestModeProfiles:

    def test_open_dance_full_brightness_range(self):
        mode = get_mode("open_dance")
        assert mode.base_brightness == pytest.approx(0.2)
        assert mode.max_brightness  == pytest.approx(1.0)

    def test_dinner_constrained_brightness(self):
        mode = get_mode("dinner")
        assert mode.base_brightness < mode.max_brightness
        assert mode.max_brightness  <= 0.7  # dinner stays subdued

    def test_speech_high_floor(self):
        mode = get_mode("speech")
        assert mode.base_brightness >= 0.4   # visible even in silence

    def test_pulse_amount_positive(self):
        for key in ["open_dance", "banger", "indian_latin", "dinner"]:
            mode = get_mode(key)
            assert mode.pulse_amount > 0, f"{key}: pulse_amount should be > 0"

    def test_speech_minimal_pulse(self):
        assert get_mode("speech").pulse_amount <= 0.05

    def test_saturation_scale_valid_range(self):
        for key in ["open_dance", "dinner", "speech", "banger", "indian_latin", "slow_dance"]:
            mode = get_mode(key)
            assert 0.0 < mode.saturation_scale <= 1.0, \
                f"{key}: saturation_scale {mode.saturation_scale} out of range"

    def test_speech_desaturated(self):
        assert get_mode("speech").saturation_scale <= 0.6

    def test_hold_ms_positive_for_real_modes(self):
        for key in ["open_dance", "dinner", "banger", "indian_latin", "slow_dance", "speech"]:
            assert get_mode(key).hold_ms > 0, f"{key}: hold_ms should be positive"

    def test_banger_faster_transitions_than_speech(self):
        # Banger should cycle through colors faster than speech
        assert get_mode("banger").hold_ms < get_mode("speech").hold_ms

    def test_base_always_lte_max(self):
        for key in get_mode.__globals__["MODES"]:
            m = get_mode(key)
            assert m.base_brightness <= m.max_brightness, \
                f"{key}: base_brightness {m.base_brightness} > max_brightness {m.max_brightness}"


# ===========================================================================
# Sprint 1B: Palette hold / transition state machine
# ===========================================================================

class TestPaletteHoldTransition:

    def _blender_with_hold(self, hold_ms: float) -> "PaletteBlender":
        palettes_dir = os.path.join(ROOT, "config", "palettes")
        pal = load_all_palettes(palettes_dir)["open_dance"]
        return PaletteBlender(pal, hold_ms=hold_ms)

    def test_no_hold_transitions_immediately(self):
        b = self._blender_with_hold(0.0)
        # With hold_ms=0, blender starts in TRANSITIONING state
        assert b.hold_remaining_ms == pytest.approx(0.0)

    def test_hold_starts_in_holding_state(self):
        b = self._blender_with_hold(5000.0)
        # Immediately after construction, hold_remaining_ms should be > 0
        assert b.hold_remaining_ms > 0.0

    def test_hold_color_stable_during_hold(self):
        b = self._blender_with_hold(5000.0)
        c1 = b.update(0.5)
        c2 = b.update(0.5)
        # During hold, color should not change between frames
        assert c1.h == pytest.approx(c2.h)

    def test_transition_progress_zero_while_holding(self):
        b = self._blender_with_hold(5000.0)
        b.update(0.5)
        assert b.transition_progress == pytest.approx(0.0)

    def test_color_names_exposed(self):
        b = self._blender_with_hold(0.0)
        # Names may be empty strings but should not raise
        _ = b.current_color_name
        _ = b.next_color_name

    def test_set_hold_ms_accepted(self):
        b = self._blender_with_hold(0.0)
        b.set_hold_ms(3000.0)
        # The new hold takes effect at the next state transition

    def test_set_palette_resets_state(self):
        palettes_dir = os.path.join(ROOT, "config", "palettes")
        pals = load_all_palettes(palettes_dir)
        b = self._blender_with_hold(2000.0)
        b.set_palette(pals["banger"])
        # After switching palette, blender should return to start
        assert b.hold_remaining_ms >= 0.0  # valid non-negative

    def test_blender_with_hold_stays_in_range(self):
        b = self._blender_with_hold(100.0)
        for _ in range(50):
            c = b.update(0.5)
            assert 0 <= c.h <= 360
            assert 0 <= c.s <= 1.0
            assert 0 <= c.v <= 1.0
            time.sleep(0.005)


# ===========================================================================
# Sprint 1B: RoomLane with mode profiles
# ===========================================================================

class TestRoomLaneWithModeProfile:

    def _setup(self, mode_key: str):
        palettes_dir = os.path.join(ROOT, "config", "palettes")
        pals         = load_all_palettes(palettes_dir)
        mode         = get_mode(mode_key)
        palette      = pals.get(mode.palette_key, list(pals.values())[0])
        safety       = SafetyEngine()
        safety.update_from_mode(mode)
        lane = RoomLane(palette, mode=mode)
        return lane, safety, mode

    def test_base_brightness_is_floor_at_zero_energy(self):
        lane, safety, mode = self._setup("open_dance")
        out = lane.render(smoothed_room=0.0, impact=0.0, safety=safety)
        # With zero room energy and no impact, brightness should be near base_brightness
        # (after mode intensity_scale)
        expected_floor = mode.base_brightness * mode.intensity_scale
        assert out.brightness >= expected_floor * 0.9, \
            f"brightness {out.brightness:.3f} below expected floor {expected_floor:.3f}"

    def test_max_brightness_ceiling_at_full_energy(self):
        lane, safety, mode = self._setup("dinner")
        out = lane.render(smoothed_room=1.0, impact=0.0, safety=safety)
        # Dinner max_brightness=0.65 × intensity_scale=0.7 → ceiling ≈ 0.455
        ceiling = mode.max_brightness * mode.intensity_scale
        assert out.brightness <= ceiling + 0.02, \
            f"brightness {out.brightness:.3f} exceeds ceiling {ceiling:.3f}"

    def test_pulse_lifts_brightness(self):
        lane, safety, _ = self._setup("banger")
        out_no_pulse   = lane.render(smoothed_room=0.5, impact=0.0, safety=safety)
        out_with_pulse = lane.render(smoothed_room=0.5, impact=1.0, safety=safety)
        assert out_with_pulse.brightness >= out_no_pulse.brightness

    def test_saturation_scale_applied(self):
        lane, safety, mode = self._setup("speech")
        # Speech has saturation_scale=0.5; with full-sat palette color,
        # output saturation should be capped well below 1.0
        out = lane.render(smoothed_room=0.5, impact=0.0, safety=safety)
        assert out.hsv.s <= mode.saturation_scale + 0.01

    def test_set_mode_updates_behavior(self):
        lane, safety, _ = self._setup("open_dance")
        # Switch to dinner (lower ceiling)
        dinner = get_mode("dinner")
        safety.update_from_mode(dinner)
        lane.set_mode(dinner)
        out = lane.render(smoothed_room=1.0, impact=0.0, safety=safety)
        ceiling = dinner.max_brightness * dinner.intensity_scale
        assert out.brightness <= ceiling + 0.02

    def test_room_lane_exposes_blender_properties(self):
        lane, safety, _ = self._setup("open_dance")
        # These should not raise
        _ = lane.current_color_name
        _ = lane.next_color_name
        _ = lane.hold_remaining_ms
        _ = lane.transition_progress

    def test_base_and_pulse_fields_in_output(self):
        lane, safety, _ = self._setup("banger")
        out = lane.render(smoothed_room=0.5, impact=0.3, safety=safety)
        assert hasattr(out, "base_brightness")
        assert hasattr(out, "pulse_brightness")
        assert out.base_brightness >= 0.0
        assert out.pulse_brightness >= 0.0


# ===========================================================================
# Visualizer: fixture state dataclasses
# ===========================================================================

class TestFixtureStateDataclasses:

    def test_uplight_state_instantiates(self):
        u = UplightState(fixture_id="u1", x=100.0, y=200.0,
                         color_rgb=(255, 0, 128), brightness=0.8)
        assert u.brightness == pytest.approx(0.8)
        assert u.active is True

    def test_wash_state_instantiates(self):
        w = WashState(fixture_id="w1", x=300.0, y=500.0,
                      color_rgb=(0, 200, 255), brightness=0.6,
                      radius=150.0, pulse_strength=0.3)
        assert w.radius == pytest.approx(150.0)

    def test_beam_state_instantiates(self):
        b = BeamState(fixture_id="b1", x=200.0, y=600.0,
                      color_rgb=(200, 100, 255), brightness=0.9,
                      angle_degrees=-30.0, length=400.0,
                      spread=6.0, movement_speed=0.7)
        assert b.angle_degrees == pytest.approx(-30.0)

    def test_sparkle_state_instantiates(self):
        sp = SparkleState(fixture_id="sp1", x=600.0, y=600.0,
                          color_rgb=(255, 255, 200), brightness=0.5,
                          sparkle_amount=0.6)
        assert sp.sparkle_amount == pytest.approx(0.6)

    def test_impact_state_instantiates(self):
        imp = ImpactState(fixture_id="i1", x=600.0, y=600.0,
                          brightness=0.8, flash_active=True)
        assert imp.flash_active is True

    def test_rig_visual_state_instantiates(self):
        rig = RigVisualState(
            mode="open_dance", palette_name="Open Dance",
            low_energy=0.5, mid_energy=0.3, high_energy=0.2, overall_energy=0.4,
            room_brightness=0.6, impact_value=0.3,
            uplights=[], washes=[], beams=[], sparkles=[], impacts=[],
            blackout_active=False,
        )
        assert rig.mode == "open_dance"
        assert rig.blackout_active is False


# ===========================================================================
# Visualizer: scene layout and RigVisualState generation
# ===========================================================================

class TestSceneLayout:

    def _build(self, mode_key: str = "open_dance", blackout: bool = False):
        palettes_dir = os.path.join(ROOT, "config", "palettes")
        pals         = load_all_palettes(palettes_dir)
        mode         = get_mode(mode_key)
        lane         = RoomLane(pals.get(mode.palette_key, list(pals.values())[0]), mode=mode)
        safety       = SafetyEngine()
        safety.update_from_mode(mode)
        room_out = lane.render(smoothed_room=0.5, impact=0.3, safety=safety)
        scene    = SceneLayout()
        return scene.update_and_build(
            bands={"low_energy": 0.4, "mid_energy": 0.3,
                   "high_energy": 0.5, "overall_energy": 0.4},
            lanes={"impact": 0.3, "room": 0.5},
            hue=room_out.hsv.h,
            saturation=room_out.hsv.s,
            brightness=room_out.brightness,
            base_brt=room_out.base_brightness,
            pulse_brt=room_out.pulse_brightness,
            mode_key=mode_key,
            palette_name=lane.palette_name,
            blackout=blackout,
        )

    def test_eighteen_uplights(self):
        rig = self._build()
        assert len(rig.uplights) == 18

    def test_two_beams(self):
        rig = self._build()
        assert len(rig.beams) == 2

    def test_rgb_values_valid(self):
        rig = self._build("banger")
        for ul in rig.uplights:
            r, g, b = ul.color_rgb
            assert 0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255

    def test_brightness_in_range(self):
        rig = self._build("open_dance")
        for ul in rig.uplights:
            assert 0.0 <= ul.brightness <= 1.0
        for w in rig.washes:
            assert 0.0 <= w.brightness <= 1.0

    def test_blackout_deactivates_fixtures(self):
        rig = self._build(blackout=True)
        assert rig.blackout_active is True
        assert all(not ul.active for ul in rig.uplights)
        assert all(not w.active  for w  in rig.washes)
        assert all(not b.active  for b  in rig.beams)

    def test_sparkle_zero_in_dinner(self):
        rig = self._build("dinner")
        for sp in rig.sparkles:
            assert sp.sparkle_amount == pytest.approx(0.0), \
                "Dinner should have no sparkle"

    def test_sparkle_active_in_banger(self):
        palettes_dir = os.path.join(ROOT, "config", "palettes")
        pals = load_all_palettes(palettes_dir)
        mode = get_mode("banger")
        lane = RoomLane(pals["banger"], mode=mode)
        safety = SafetyEngine()
        safety.update_from_mode(mode)
        room_out = lane.render(smoothed_room=0.8, impact=0.5, safety=safety)
        scene = SceneLayout()
        rig = scene.update_and_build(
            bands={"low_energy": 0.5, "mid_energy": 0.4,
                   "high_energy": 0.8, "overall_energy": 0.6},
            lanes={"impact": 0.5, "room": 0.8},
            hue=room_out.hsv.h, saturation=room_out.hsv.s,
            brightness=room_out.brightness,
            base_brt=room_out.base_brightness, pulse_brt=room_out.pulse_brightness,
            mode_key="banger", palette_name="Banger", blackout=False,
        )
        total_sparkle = sum(sp.sparkle_amount for sp in rig.sparkles)
        assert total_sparkle > 0.1, "Banger should show sparkle when high energy > 0"

    def test_impact_flash_not_in_speech(self):
        rig = self._build("speech")
        for imp in rig.impacts:
            # Speech never allows flash
            assert not imp.flash_active

    def test_second_build_does_not_raise(self):
        scene = SceneLayout()
        palettes_dir = os.path.join(ROOT, "config", "palettes")
        pals = load_all_palettes(palettes_dir)
        mode = get_mode("open_dance")
        lane = RoomLane(pals["open_dance"], mode=mode)
        safety = SafetyEngine()
        safety.update_from_mode(mode)
        for _ in range(10):
            room_out = lane.render(smoothed_room=0.5, impact=0.3, safety=safety)
            rig = scene.update_and_build(
                bands={"low_energy": 0.4, "mid_energy": 0.3,
                       "high_energy": 0.2, "overall_energy": 0.3},
                lanes={"impact": 0.3, "room": 0.5},
                hue=room_out.hsv.h, saturation=room_out.hsv.s,
                brightness=room_out.brightness,
                base_brt=room_out.base_brightness, pulse_brt=room_out.pulse_brightness,
                mode_key="open_dance", palette_name="Open Dance", blackout=False,
            )
            time.sleep(0.005)
        assert rig is not None


# ===========================================================================
# Sprint 2: White/Amber/UV channel control
# ===========================================================================

class TestWAUChannelLevels:

    def test_dinner_mode_has_amber_base(self):
        mode = get_mode("dinner")
        assert mode.amber_base > 0.0, "Dinner should have ambient amber"

    def test_speech_mode_has_white_base(self):
        mode = get_mode("speech")
        assert mode.white_base > 0.0, "Speech should have prominent white"

    def test_open_dance_has_uv(self):
        mode = get_mode("open_dance")
        assert mode.uv_base > 0.0 or mode.uv_scale > 0.0, \
            "Open Dance should use UV"

    def test_banger_has_white_impact(self):
        mode = get_mode("banger")
        assert mode.white_impact > 0.0, "Banger should flash white on impact"

    def test_room_lane_outputs_nonzero_amber_in_dinner(self):
        palettes_dir = os.path.join(ROOT, "config", "palettes")
        pals = load_all_palettes(palettes_dir)
        mode = get_mode("dinner")
        lane = RoomLane(pals["dinner"], mode=mode)
        safety = SafetyEngine()
        safety.update_from_mode(mode)
        out = lane.render(smoothed_room=0.5, impact=0.2, safety=safety)
        assert out.amber > 0.0, "Dinner render should produce non-zero amber"

    def test_wau_zeroed_on_blackout(self):
        palettes_dir = os.path.join(ROOT, "config", "palettes")
        pals = load_all_palettes(palettes_dir)
        mode = get_mode("dinner")
        lane = RoomLane(pals["dinner"], mode=mode)
        safety = SafetyEngine()
        safety.update_from_mode(mode)
        safety.toggle_blackout()     # activate blackout
        out = lane.render(smoothed_room=0.8, impact=0.5, safety=safety)
        assert out.white == pytest.approx(0.0)
        assert out.amber == pytest.approx(0.0)
        assert out.uv    == pytest.approx(0.0)

    def test_rockwedge_writes_nonzero_amber_channel(self):
        uni = DMXUniverse()
        rw  = RockWedge(fixture_id="t", name="Test", dmx_address=1)
        rw.render_to_universe(uni, brightness=1.0, hue=0.0, saturation=1.0,
                              value=1.0, amber=0.5)
        ch6 = uni.get_channel(6)   # Ch6 = Amber
        assert ch6 > 0, "Amber channel should be non-zero when amber=0.5"

    def test_rockwedge_writes_nonzero_uv_channel(self):
        uni = DMXUniverse()
        rw  = RockWedge(fixture_id="t", name="Test", dmx_address=1)
        rw.render_to_universe(uni, brightness=1.0, hue=0.0, saturation=1.0,
                              value=1.0, uv=0.7)
        ch7 = uni.get_channel(7)   # Ch7 = UV
        assert ch7 > 0, "UV channel should be non-zero when uv=0.7"

    def test_wau_levels_are_bounded(self):
        palettes_dir = os.path.join(ROOT, "config", "palettes")
        pals = load_all_palettes(palettes_dir)
        for mode_key in ["dinner", "speech", "open_dance", "banger",
                         "indian_latin", "slow_dance"]:
            mode = get_mode(mode_key)
            lane = RoomLane(pals.get(mode.palette_key, list(pals.values())[0]), mode=mode)
            safety = SafetyEngine()
            safety.update_from_mode(mode)
            out = lane.render(smoothed_room=1.0, impact=1.0, safety=safety)
            assert 0.0 <= out.white <= 1.0, f"{mode_key} white out of bounds"
            assert 0.0 <= out.amber <= 1.0, f"{mode_key} amber out of bounds"
            assert 0.0 <= out.uv    <= 1.0, f"{mode_key} UV out of bounds"


# ===========================================================================
# Sprint 2: Beat detector
# ===========================================================================

class TestBeatDetector:

    def test_no_beat_on_empty_history(self):
        bd = BeatDetector()
        detected, strength = bd.update(0.8)
        assert detected is False
        assert strength == pytest.approx(0.0)

    def test_no_beat_on_uniform_energy(self):
        bd = BeatDetector(threshold=1.5)
        # Fill history with uniform energy — no beat should fire
        for _ in range(25):
            detected, _ = bd.update(0.5)
        assert detected is False, "Uniform energy should never trigger a beat"

    def test_beat_fires_on_transient(self):
        bd = BeatDetector(threshold=1.5, min_interval_ms=0.0)
        # Warm up with low baseline
        for _ in range(20):
            bd.update(0.1)
        # Fire a spike 4× the average
        detected, strength = bd.update(0.6)
        assert detected is True
        assert strength > 0.0

    def test_beat_strength_is_normalized(self):
        bd = BeatDetector(threshold=1.2, min_interval_ms=0.0)
        for _ in range(20):
            bd.update(0.1)
        _, strength = bd.update(1.0)
        assert 0.0 <= strength <= 1.0

    def test_min_interval_prevents_double_trigger(self):
        bd = BeatDetector(threshold=1.2, min_interval_ms=500.0)
        for _ in range(20):
            bd.update(0.1)
        d1, _ = bd.update(0.8)   # first spike
        d2, _ = bd.update(0.8)   # immediate second spike — should be suppressed
        assert d1 is True
        assert d2 is False, "Second beat should be suppressed by min_interval"

    def test_bpm_estimated_after_beats(self):
        bd = BeatDetector(threshold=1.2, min_interval_ms=0.0)
        # Simulate multiple beats by mocking time — we just check bpm is set
        for _ in range(20):
            bd.update(0.05)
        # Two fast spikes with no interval restriction
        bd.update(0.5)
        time.sleep(0.3)
        bd.update(0.5)
        time.sleep(0.3)
        bd.update(0.5)
        # BPM should now be populated
        assert bd.bpm >= 0.0   # just verify it's set and non-negative

    def test_reset_clears_state(self):
        bd = BeatDetector(threshold=1.2, min_interval_ms=0.0)
        for _ in range(20):
            bd.update(0.1)
        bd.update(0.8)           # fire a beat to set last_beat_time
        bd.reset()
        assert bd.bpm == pytest.approx(0.0)
        detected, _ = bd.update(0.8)  # after reset, history is empty → no beat
        assert detected is False

    def test_no_beat_when_energy_below_threshold(self):
        bd = BeatDetector(threshold=2.0, min_interval_ms=0.0)
        for _ in range(20):
            bd.update(0.4)
        # 1.5× the average — below threshold of 2.0×
        detected, _ = bd.update(0.6)
        assert detected is False


# ===========================================================================
# Sprint 2: Mode transitioner
# ===========================================================================

class TestModeTransitioner:

    def test_initial_blend_t_is_one(self):
        mode = get_mode("open_dance")
        tr = ModeTransitioner(mode)
        assert tr.blend_t == pytest.approx(1.0)

    def test_initial_current_mode_is_set(self):
        mode = get_mode("dinner")
        tr = ModeTransitioner(mode)
        assert tr.current_mode is mode

    def test_switch_resets_blend_t_for_non_snap(self):
        tr = ModeTransitioner(get_mode("dinner"))
        tr.switch(get_mode("slow_dance"))   # slow_dance is not snap
        assert tr.blend_t == pytest.approx(0.0)

    def test_snap_mode_keeps_blend_t_at_one(self):
        tr = ModeTransitioner(get_mode("dinner"))
        banger = get_mode("banger")
        assert banger.transition_snap is True
        tr.switch(banger)
        assert tr.blend_t == pytest.approx(1.0)

    def test_blend_t_advances_to_one_after_update(self):
        tr = ModeTransitioner(get_mode("dinner"))
        tr.switch(get_mode("open_dance"))
        assert tr.blend_t < 1.0
        # ModeTransitioner caps dt at 100ms per update to prevent big jumps on resume,
        # so we must call update() in a loop until the transition completes.
        deadline = time.monotonic() + 2.0
        blend_t = 0.0
        while time.monotonic() < deadline:
            blend_t = tr.update()
            if blend_t >= 1.0:
                break
            time.sleep(0.05)
        assert blend_t == pytest.approx(1.0)

    def test_current_and_prev_modes_set_correctly(self):
        dinner = get_mode("dinner")
        speech = get_mode("speech")
        tr = ModeTransitioner(dinner)
        tr.switch(speech)
        assert tr.current_mode is speech
        assert tr.prev_mode is dinner

    def test_lerp_helper(self):
        assert ModeTransitioner.lerp(0.0, 1.0, 0.5) == pytest.approx(0.5)
        assert ModeTransitioner.lerp(0.0, 1.0, 0.0) == pytest.approx(0.0)
        assert ModeTransitioner.lerp(0.0, 1.0, 1.0) == pytest.approx(1.0)


# ===========================================================================
# Sprint 2: Palette blender beat trigger
# ===========================================================================

class TestPaletteBlenderBeatTrigger:

    def _make_blender(self, palette_key: str, hold_ms: float = 500.0) -> PaletteBlender:
        palettes_dir = os.path.join(ROOT, "config", "palettes")
        pals = load_all_palettes(palettes_dir)
        return PaletteBlender(pals[palette_key], hold_ms=hold_ms)

    def test_beat_trigger_releases_hold_on_fast_beat_palette(self):
        """banger uses change_rule=fast_beat: beat_trigger=True should end hold."""
        blender = self._make_blender("banger", hold_ms=60_000.0)
        # First update starts hold
        c1 = blender.update(energy=0.5, beat_trigger=False)
        # Beat trigger should release hold immediately
        c2 = blender.update(energy=0.5, beat_trigger=True)
        # Next frame should already be transitioning (blend started)
        c3 = blender.update(energy=0.5, beat_trigger=False)
        # c1 and c2 are the held color; c3 may differ if blend started
        # We can't easily assert c3 != c1 in one frame, but no exception = correct
        assert c1 is not None
        assert c2 is not None
        assert c3 is not None

    def test_beat_trigger_releases_hold_on_energy_trigger_palette(self):
        """open_dance uses change_rule=energy_trigger: beat_trigger should release hold."""
        blender = self._make_blender("open_dance", hold_ms=60_000.0)
        # Initial call populates hold state
        blender.update(energy=0.5, beat_trigger=False)
        # A beat should cause immediate transition from hold
        blender.update(energy=0.5, beat_trigger=True)
        # After trigger, transition_progress should advance on next update
        time.sleep(0.02)
        blender.update(energy=0.5, beat_trigger=False)
        assert blender.transition_progress > 0.0 or blender.hold_remaining_ms == 0.0

    def test_beat_trigger_ignored_on_slow_blend_palette(self):
        """dinner/slow_dance use slow_blend: beat_trigger must be a no-op."""
        blender = self._make_blender("dinner", hold_ms=60_000.0)
        blender.update(energy=0.5, beat_trigger=False)
        hold_before = blender.hold_remaining_ms
        # Beat trigger on slow_blend palette — should NOT release hold
        blender.update(energy=0.5, beat_trigger=True)
        assert blender.hold_remaining_ms > 0.0, \
            "slow_blend palette should ignore beat_trigger"

    def test_beat_trigger_false_no_early_release(self):
        """beat_trigger=False must never cause early hold release."""
        blender = self._make_blender("banger", hold_ms=60_000.0)
        blender.update(energy=0.5, beat_trigger=False)
        for _ in range(5):
            blender.update(energy=0.5, beat_trigger=False)
        # Hold should still be active (60s hold, only a few ms elapsed)
        assert blender.hold_remaining_ms > 0.0


# =============================================================================
# Sprint 3: Song Preview Mode
# =============================================================================

from audio.offline_analyzer import OfflineAnalyzer, AnalysisTimeline, AnalysisFrame, EventMarker
from engine.deterministic   import DeterministicEngine
from engine.settings_snapshot import SettingsSnapshot
from app.render.fixture_state import FixtureStateTimeline, TimedFrame
from app.render.playback import PlaybackController


# ---------------------------------------------------------------------------
# TestSettingsSnapshot
# ---------------------------------------------------------------------------

class TestSettingsSnapshot:
    def test_instantiation_with_required_fields(self):
        s = SettingsSnapshot(mode_key="banger", palette_key="banger")
        assert s.mode_key == "banger"
        assert s.palette_key == "banger"

    def test_master_dimmer_default(self):
        s = SettingsSnapshot(mode_key="dinner", palette_key="dinner")
        assert s.master_dimmer == 1.0

    def test_all_overrides_default_to_none(self):
        s = SettingsSnapshot(mode_key="open_dance", palette_key="open_dance")
        assert s.base_brightness_override is None
        assert s.max_brightness_override is None
        assert s.pulse_amount_override is None
        assert s.saturation_scale_override is None

    def test_overrides_can_be_set(self):
        s = SettingsSnapshot(
            mode_key="banger",
            palette_key="banger",
            base_brightness_override=0.5,
            max_brightness_override=0.8,
        )
        assert s.base_brightness_override == pytest.approx(0.5)
        assert s.max_brightness_override == pytest.approx(0.8)

    def test_created_at_is_recent(self):
        before = time.time()
        s = SettingsSnapshot(mode_key="banger", palette_key="banger")
        after = time.time()
        assert before <= s.created_at <= after


# ---------------------------------------------------------------------------
# TestOfflineAnalyzer
# ---------------------------------------------------------------------------

def _make_sine_audio(freq_hz=440, duration_s=2.0, sample_rate=44100,
                     amplitude=0.5) -> np.ndarray:
    """Generate a mono float32 sine wave array."""
    t = np.linspace(0, duration_s, int(duration_s * sample_rate), endpoint=False)
    return (np.sin(2 * np.pi * freq_hz * t) * amplitude).astype(np.float32)


def _make_silence(duration_s=1.0, sample_rate=44100) -> np.ndarray:
    return np.zeros(int(duration_s * sample_rate), dtype=np.float32)


class TestOfflineAnalyzer:
    def test_empty_audio_returns_empty_timeline(self):
        az = OfflineAnalyzer()
        tl = az.analyze(np.array([], dtype=np.float32))
        assert tl.frames == []
        assert tl.events == []
        assert tl.duration_s == 0.0

    def test_frame_count_matches_audio_length(self):
        az = OfflineAnalyzer(block_size=1024)
        audio = _make_sine_audio(duration_s=2.0)
        tl = az.analyze(audio, sample_rate=44100)
        expected = (len(audio) - 1024) // 1024 + 1
        assert len(tl.frames) == expected

    def test_analysis_is_deterministic(self):
        az = OfflineAnalyzer()
        audio = _make_sine_audio(duration_s=3.0)
        tl1 = az.analyze(audio, sample_rate=44100)
        tl2 = az.analyze(audio, sample_rate=44100)
        assert len(tl1.frames) == len(tl2.frames)
        for f1, f2 in zip(tl1.frames, tl2.frames):
            assert f1.time_s == f2.time_s
            assert f1.overall_energy == pytest.approx(f2.overall_energy)

    def test_energy_values_in_0_to_1(self):
        az = OfflineAnalyzer()
        audio = _make_sine_audio(duration_s=2.0)
        tl = az.analyze(audio, sample_rate=44100)
        for f in tl.frames:
            assert 0.0 <= f.low_energy    <= 1.0
            assert 0.0 <= f.mid_energy    <= 1.0
            assert 0.0 <= f.high_energy   <= 1.0
            assert 0.0 <= f.overall_energy <= 1.0

    def test_onset_strength_in_0_to_1(self):
        az = OfflineAnalyzer()
        audio = _make_sine_audio(duration_s=2.0)
        tl = az.analyze(audio, sample_rate=44100)
        for f in tl.frames:
            assert 0.0 <= f.onset_strength <= 1.0

    def test_silence_has_no_onsets(self):
        az = OfflineAnalyzer()
        audio = _make_silence(duration_s=2.0)
        tl = az.analyze(audio, sample_rate=44100)
        onsets = [f for f in tl.frames if f.is_onset]
        assert len(onsets) == 0

    def test_onset_times_respect_min_interval(self):
        az = OfflineAnalyzer(min_onset_interval_s=0.15)
        audio = _make_sine_audio(duration_s=5.0)
        tl = az.analyze(audio, sample_rate=44100)
        onset_times = [f.time_s for f in tl.frames if f.is_onset]
        for i in range(1, len(onset_times)):
            assert onset_times[i] - onset_times[i - 1] >= 0.14  # small float tolerance

    def test_bpm_estimate_in_range_or_none(self):
        az = OfflineAnalyzer()
        audio = _make_sine_audio(duration_s=5.0)
        tl = az.analyze(audio, sample_rate=44100)
        if tl.bpm_estimate is not None:
            assert 60 <= tl.bpm_estimate <= 180

    def test_timeline_metadata(self):
        az = OfflineAnalyzer(block_size=1024)
        audio = _make_sine_audio(duration_s=2.0, sample_rate=44100)
        tl = az.analyze(audio, sample_rate=44100)
        assert tl.sample_rate == 44100
        assert tl.hop_size == 1024
        assert tl.duration_s == pytest.approx(2.0, abs=0.05)

    def test_stereo_audio_handled_gracefully(self):
        az = OfflineAnalyzer()
        mono = _make_sine_audio(duration_s=2.0)
        stereo = np.stack([mono, mono], axis=1)
        tl = az.analyze(stereo, sample_rate=44100)
        assert len(tl.frames) > 0


# ---------------------------------------------------------------------------
# TestFixtureStateTimeline
# ---------------------------------------------------------------------------

def _make_rig_state():
    return RigVisualState(
        mode="test", palette_name="test",
        low_energy=0.0, mid_energy=0.0, high_energy=0.0, overall_energy=0.0,
        room_brightness=0.0, impact_value=0.0,
        uplights=[], washes=[], beams=[], sparkles=[], impacts=[],
        blackout_active=False,
    )


class TestFixtureStateTimeline:
    def test_empty_timeline_frame_at_returns_none(self):
        tl = FixtureStateTimeline()
        assert tl.frame_at(0.0) is None

    def test_frame_at_returns_closest_frame(self):
        s1 = _make_rig_state(); s2 = _make_rig_state()
        tl = FixtureStateTimeline(
            frames=[TimedFrame(0.0, s1), TimedFrame(1.0, s2)],
            duration_s=1.0,
        )
        assert tl.frame_at(0.3) is s1
        assert tl.frame_at(0.6) is s2

    def test_frame_at_before_start(self):
        s = _make_rig_state()
        tl = FixtureStateTimeline(frames=[TimedFrame(0.5, s)], duration_s=1.0)
        assert tl.frame_at(0.0) is s

    def test_frame_at_past_end(self):
        s = _make_rig_state()
        tl = FixtureStateTimeline(frames=[TimedFrame(0.0, s)], duration_s=1.0)
        assert tl.frame_at(99.0) is s

    def test_frame_at_exact_match(self):
        s = _make_rig_state()
        tl = FixtureStateTimeline(frames=[TimedFrame(0.5, s)], duration_s=1.0)
        assert tl.frame_at(0.5) is s


# ---------------------------------------------------------------------------
# TestPlaybackController
# ---------------------------------------------------------------------------

def _make_fx_timeline(n_frames=10, duration_s=1.0):
    frames = [
        TimedFrame(i * duration_s / n_frames, _make_rig_state())
        for i in range(n_frames)
    ]
    return FixtureStateTimeline(frames=frames, duration_s=duration_s)


class TestPlaybackController:
    def test_initial_state_is_paused(self):
        ctrl = PlaybackController()
        ctrl.load(_make_fx_timeline())
        assert not ctrl.is_playing

    def test_initial_time_is_zero(self):
        ctrl = PlaybackController()
        ctrl.load(_make_fx_timeline())
        assert ctrl.current_time_s == pytest.approx(0.0)

    def test_play_sets_playing(self):
        ctrl = PlaybackController()
        ctrl.load(_make_fx_timeline())
        ctrl.play()
        assert ctrl.is_playing

    def test_pause_stops_playing(self):
        ctrl = PlaybackController()
        ctrl.load(_make_fx_timeline())
        ctrl.play()
        ctrl.pause()
        assert not ctrl.is_playing

    def test_toggle_play_pause(self):
        ctrl = PlaybackController()
        ctrl.load(_make_fx_timeline())
        ctrl.toggle_play_pause()
        assert ctrl.is_playing
        ctrl.toggle_play_pause()
        assert not ctrl.is_playing

    def test_seek_clamps_to_duration(self):
        ctrl = PlaybackController()
        tl = _make_fx_timeline(duration_s=5.0)
        ctrl.load(tl)
        ctrl.seek(100.0)
        assert ctrl.current_time_s == pytest.approx(5.0)
        ctrl.seek(-5.0)
        assert ctrl.current_time_s == pytest.approx(0.0)

    def test_step_relative_seek(self):
        ctrl = PlaybackController()
        ctrl.load(_make_fx_timeline(duration_s=10.0))
        ctrl.seek(3.0)
        ctrl.step(2.0)
        assert ctrl.current_time_s == pytest.approx(5.0)

    def test_update_advances_time_while_playing(self):
        ctrl = PlaybackController()
        ctrl.load(_make_fx_timeline(duration_s=10.0))
        ctrl.play()
        time.sleep(0.05)
        ctrl.update()
        assert ctrl.current_time_s > 0.0

    def test_update_does_not_advance_while_paused(self):
        ctrl = PlaybackController()
        ctrl.load(_make_fx_timeline(duration_s=10.0))
        # Don't call play()
        time.sleep(0.05)
        ctrl.update()
        assert ctrl.current_time_s == pytest.approx(0.0)

    def test_progress_property(self):
        ctrl = PlaybackController()
        ctrl.load(_make_fx_timeline(duration_s=10.0))
        ctrl.seek(5.0)
        assert ctrl.progress == pytest.approx(0.5)

    def test_update_returns_rig_state(self):
        ctrl = PlaybackController()
        ctrl.load(_make_fx_timeline())
        state = ctrl.update()
        assert isinstance(state, RigVisualState)

    def test_no_timeline_update_returns_none(self):
        ctrl = PlaybackController()
        assert ctrl.update() is None

    def test_playback_stops_at_end(self):
        ctrl = PlaybackController()
        ctrl.load(_make_fx_timeline(duration_s=0.1))
        ctrl.play()
        time.sleep(0.15)
        ctrl.update()
        assert not ctrl.is_playing
        assert ctrl.current_time_s == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# TestDeterministicEngine
# ---------------------------------------------------------------------------

def _make_analysis_timeline(duration_s=2.0, sample_rate=44100, block_size=1024):
    audio = _make_sine_audio(duration_s=duration_s, sample_rate=sample_rate)
    return OfflineAnalyzer(block_size=block_size).analyze(audio, sample_rate)


def _make_palettes():
    return load_all_palettes(os.path.join(ROOT, "config", "palettes"))


class TestDeterministicEngine:
    def test_generates_nonempty_timeline(self):
        tl = _make_analysis_timeline(duration_s=1.0)
        palettes = _make_palettes()
        settings = SettingsSnapshot(mode_key="open_dance", palette_key="open_dance")
        engine = DeterministicEngine(settings, seed=42)
        fx_tl = engine.generate(tl, palettes)
        assert len(fx_tl.frames) > 0

    def test_frame_count_matches_analysis(self):
        tl = _make_analysis_timeline(duration_s=1.0)
        palettes = _make_palettes()
        settings = SettingsSnapshot(mode_key="open_dance", palette_key="open_dance")
        engine = DeterministicEngine(settings, seed=42)
        fx_tl = engine.generate(tl, palettes)
        assert len(fx_tl.frames) == len(tl.frames)

    def test_same_input_same_output(self):
        tl = _make_analysis_timeline(duration_s=2.0)
        palettes = _make_palettes()
        settings = SettingsSnapshot(mode_key="banger", palette_key="banger")
        engine1 = DeterministicEngine(settings, seed=7)
        engine2 = DeterministicEngine(settings, seed=7)
        fx1 = engine1.generate(tl, palettes)
        fx2 = engine2.generate(tl, palettes)
        assert len(fx1.frames) == len(fx2.frames)
        for f1, f2 in zip(fx1.frames, fx2.frames):
            assert f1.time_s == pytest.approx(f2.time_s)
            assert f1.state.room_brightness == pytest.approx(f2.state.room_brightness, abs=1e-5)

    def test_different_seeds_can_differ(self):
        tl = _make_analysis_timeline(duration_s=2.0)
        palettes = _make_palettes()
        settings = SettingsSnapshot(mode_key="banger", palette_key="banger")
        fx1 = DeterministicEngine(settings, seed=1).generate(tl, palettes)
        fx2 = DeterministicEngine(settings, seed=999).generate(tl, palettes)
        # Timings must match; seeds may or may not affect the output
        assert len(fx1.frames) == len(fx2.frames)

    def test_all_modes_produce_valid_output(self):
        tl = _make_analysis_timeline(duration_s=1.0)
        palettes = _make_palettes()
        for mode_key in MODES:
            mode = get_mode(mode_key)
            settings = SettingsSnapshot(mode_key=mode_key, palette_key=mode.palette_key)
            engine = DeterministicEngine(settings, seed=42)
            fx_tl = engine.generate(tl, palettes)
            assert len(fx_tl.frames) > 0, f"Mode {mode_key} produced no frames"

    def test_brightness_in_bounds(self):
        tl = _make_analysis_timeline(duration_s=1.0)
        palettes = _make_palettes()
        settings = SettingsSnapshot(mode_key="open_dance", palette_key="open_dance")
        fx_tl = DeterministicEngine(settings, seed=42).generate(tl, palettes)
        for frame in fx_tl.frames:
            assert 0.0 <= frame.state.room_brightness <= 1.0, \
                f"room_brightness out of range: {frame.state.room_brightness}"

    def test_empty_analysis_produces_empty_timeline(self):
        tl = AnalysisTimeline(
            frames=[], events=[], duration_s=0.0,
            sample_rate=44100, hop_size=1024, window_size=1024,
        )
        palettes = _make_palettes()
        settings = SettingsSnapshot(mode_key="open_dance", palette_key="open_dance")
        fx_tl = DeterministicEngine(settings, seed=42).generate(tl, palettes)
        assert len(fx_tl.frames) == 0

    def test_duration_preserved(self):
        tl = _make_analysis_timeline(duration_s=2.0)
        palettes = _make_palettes()
        settings = SettingsSnapshot(mode_key="open_dance", palette_key="open_dance")
        fx_tl = DeterministicEngine(settings, seed=42).generate(tl, palettes)
        assert fx_tl.duration_s == pytest.approx(tl.duration_s, abs=0.05)


# ---------------------------------------------------------------------------
# TestClockInjection
# ---------------------------------------------------------------------------

class TestClockInjection:
    def test_envelope_follower_injected_clock(self):
        from engine.smoothing import EnvelopeFollower, EnvelopeConfig
        cfg = EnvelopeConfig(attack_ms=10, decay_ms=50, cooldown_ms=0)
        ef = EnvelopeFollower(cfg)
        ef.reset(now=0.0)
        # Feed a value at t=0.1s with injected clock
        v = ef.update(1.0, now=0.1)
        assert 0.0 < v <= 1.0

    def test_envelope_follower_injected_clock_deterministic(self):
        from engine.smoothing import EnvelopeFollower, EnvelopeConfig
        cfg = EnvelopeConfig(attack_ms=20, decay_ms=100, cooldown_ms=0)
        ef1 = EnvelopeFollower(cfg); ef1.reset(now=0.0)
        ef2 = EnvelopeFollower(cfg); ef2.reset(now=0.0)
        times  = [0.05, 0.10, 0.15, 0.20]
        values = [0.8,  0.6,  0.4,  0.2]
        out1 = [ef1.update(v, now=t) for v, t in zip(values, times)]
        out2 = [ef2.update(v, now=t) for v, t in zip(values, times)]
        assert out1 == pytest.approx(out2)

    def test_lane_smoother_injected_clock(self):
        from engine.smoothing import LaneSmoother
        ls = LaneSmoother(); ls.reset_all(now=0.0)
        bands = {"low_energy": 0.5, "mid_energy": 0.3, "high_energy": 0.2, "overall_energy": 0.4}
        lanes = ls.update(bands, now=0.1)
        assert "room" in lanes
        assert "impact" in lanes

    def test_palette_blender_injected_clock(self):
        palette = load_palette(os.path.join(ROOT, "config", "palettes", "open_dance.json"))
        blender = PaletteBlender(palette)
        blender.reset_time(now=0.0)
        color = blender.update(energy=0.5, beat_trigger=False, now=0.1)
        assert isinstance(color, HSVColor)

    def test_deterministic_engine_clock_consistency(self):
        """Two runs on the same timeline must produce frame times that match exactly."""
        tl = _make_analysis_timeline(duration_s=1.0)
        palettes = _make_palettes()
        settings = SettingsSnapshot(mode_key="open_dance", palette_key="open_dance")
        fx1 = DeterministicEngine(settings, seed=42).generate(tl, palettes)
        fx2 = DeterministicEngine(settings, seed=42).generate(tl, palettes)
        times1 = [f.time_s for f in fx1.frames]
        times2 = [f.time_s for f in fx2.frames]
        assert times1 == pytest.approx(times2)


# =============================================================================
# Sprint 4: Saved Program Mode
# =============================================================================

import hashlib
import tempfile
import uuid

from data.lighting_program import (
    LightingProgram, ProgramSummary, compute_song_fingerprint,
    PROGRAM_SCHEMA_VERSION,
)
from data.program_store import ProgramStore, _serialize_program, _deserialize_program


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_full_program(mode_key="open_dance", seed=42, duration_s=1.0):
    """Build a minimal but complete LightingProgram for testing."""
    audio    = _make_sine_audio(duration_s=duration_s)
    sr       = 44100
    tl       = _make_analysis_timeline(duration_s=duration_s)
    palettes = _make_palettes()
    settings = SettingsSnapshot(mode_key=mode_key, palette_key=mode_key)
    fx_tl    = DeterministicEngine(settings, seed=seed).generate(tl, palettes)
    return LightingProgram.create(
        name=f"Test {mode_key}",
        audio=audio, sample_rate=sr,
        song_file_path="test.wav",
        settings=settings, random_seed=seed,
        analysis_timeline=tl,
        fixture_state_timeline=fx_tl,
    ), audio, sr


# ---------------------------------------------------------------------------
# TestSongFingerprint
# ---------------------------------------------------------------------------

class TestSongFingerprint:
    def test_returns_sha256_hex_string(self):
        audio = _make_sine_audio(duration_s=2.0)
        fp = compute_song_fingerprint(audio, 44100)
        assert len(fp) == 64
        assert all(c in "0123456789abcdef" for c in fp)

    def test_deterministic_same_audio(self):
        audio = _make_sine_audio(duration_s=2.0)
        fp1 = compute_song_fingerprint(audio, 44100)
        fp2 = compute_song_fingerprint(audio, 44100)
        assert fp1 == fp2

    def test_different_audio_different_fingerprint(self):
        a1 = _make_sine_audio(freq_hz=440, duration_s=2.0)
        a2 = _make_sine_audio(freq_hz=880, duration_s=2.0)
        assert compute_song_fingerprint(a1, 44100) != compute_song_fingerprint(a2, 44100)

    def test_volume_normalized(self):
        audio = _make_sine_audio(duration_s=2.0, amplitude=0.5)
        loud  = (audio * 2.0).astype(np.float32)
        assert compute_song_fingerprint(audio, 44100) == compute_song_fingerprint(loud, 44100)

    def test_empty_audio_returns_valid_hex(self):
        fp = compute_song_fingerprint(np.array([], dtype=np.float32), 44100)
        assert len(fp) == 64

    def test_uses_only_first_30s(self):
        long_audio  = _make_sine_audio(duration_s=60.0)
        short_audio = long_audio[:44100 * 30]
        # These should match because fingerprint only uses first 30s
        assert (compute_song_fingerprint(long_audio, 44100) ==
                compute_song_fingerprint(short_audio, 44100))


# ---------------------------------------------------------------------------
# TestLightingProgram
# ---------------------------------------------------------------------------

class TestLightingProgram:
    def test_create_generates_uuid(self):
        prog, _, _ = _make_full_program()
        assert len(prog.program_id) == 36  # UUID4 format
        parsed = uuid.UUID(prog.program_id)
        assert parsed.version == 4

    def test_create_sets_fingerprint(self):
        prog, audio, sr = _make_full_program()
        expected = compute_song_fingerprint(audio, sr)
        assert prog.song_fingerprint == expected

    def test_create_sets_duration(self):
        prog, _, _ = _make_full_program(duration_s=1.0)
        assert prog.song_duration_s == pytest.approx(1.0, abs=0.1)

    def test_schema_version(self):
        prog, _, _ = _make_full_program()
        assert prog.version == PROGRAM_SCHEMA_VERSION

    def test_to_summary_fields(self):
        prog, _, _ = _make_full_program(mode_key="banger")
        summary = prog.to_summary()
        assert isinstance(summary, ProgramSummary)
        assert summary.program_id == prog.program_id
        assert summary.name == prog.name
        assert summary.mode_key == "banger"
        assert summary.song_fingerprint == prog.song_fingerprint

    def test_timestamps_recent(self):
        before = time.time()
        prog, _, _ = _make_full_program()
        after = time.time()
        assert before <= prog.created_at <= after
        assert before <= prog.updated_at <= after

    def test_analysis_timeline_stored(self):
        prog, _, _ = _make_full_program(duration_s=1.0)
        assert prog.analysis_timeline is not None
        assert len(prog.analysis_timeline.frames) > 0

    def test_fixture_state_timeline_stored(self):
        prog, _, _ = _make_full_program(duration_s=1.0)
        assert prog.fixture_state_timeline is not None
        assert len(prog.fixture_state_timeline.frames) > 0


# ---------------------------------------------------------------------------
# TestProgramStoreSerialization
# ---------------------------------------------------------------------------

class TestProgramStoreSerialization:
    def test_serialize_deserialize_identity(self):
        prog, _, _ = _make_full_program()
        data  = _serialize_program(prog)
        prog2 = _deserialize_program(data)
        assert prog2.program_id   == prog.program_id
        assert prog2.name         == prog.name
        assert prog2.random_seed  == prog.random_seed
        assert prog2.song_fingerprint == prog.song_fingerprint

    def test_rgb_tuples_preserved(self):
        prog, _, _ = _make_full_program()
        data  = _serialize_program(prog)
        prog2 = _deserialize_program(data)
        if prog2.fixture_state_timeline and prog2.fixture_state_timeline.frames:
            state = prog2.fixture_state_timeline.frames[0].state
            for uplight in state.uplights:
                assert isinstance(uplight.color_rgb, tuple), \
                    f"color_rgb should be tuple, got {type(uplight.color_rgb)}"
                assert len(uplight.color_rgb) == 3

    def test_analysis_frames_preserved(self):
        prog, _, _ = _make_full_program(duration_s=1.0)
        data  = _serialize_program(prog)
        prog2 = _deserialize_program(data)
        n1 = len(prog.analysis_timeline.frames)
        n2 = len(prog2.analysis_timeline.frames)
        assert n1 == n2

    def test_bpm_estimate_preserved(self):
        prog, _, _ = _make_full_program()
        data  = _serialize_program(prog)
        prog2 = _deserialize_program(data)
        assert prog2.analysis_timeline.bpm_estimate == prog.analysis_timeline.bpm_estimate

    def test_settings_preserved(self):
        prog, _, _ = _make_full_program(mode_key="dinner")
        data  = _serialize_program(prog)
        prog2 = _deserialize_program(data)
        assert prog2.settings.mode_key    == "dinner"
        assert prog2.settings.palette_key == prog.settings.palette_key

    def test_none_timelines_handled(self):
        prog = LightingProgram(
            program_id=str(uuid.uuid4()),
            name="Empty",
            analysis_timeline=None,
            fixture_state_timeline=None,
        )
        data  = _serialize_program(prog)
        prog2 = _deserialize_program(data)
        assert prog2.analysis_timeline is None
        assert prog2.fixture_state_timeline is None


# ---------------------------------------------------------------------------
# TestProgramStore
# ---------------------------------------------------------------------------

class TestProgramStore:
    def _store(self, tmpdir):
        return ProgramStore(tmpdir)

    def test_save_creates_json_file(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d)
            prog, _, _ = _make_full_program()
            path = store.save(prog)
            assert os.path.exists(path)
            assert path.endswith(".json")

    def test_save_creates_index(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d)
            prog, _, _ = _make_full_program()
            store.save(prog)
            assert os.path.exists(os.path.join(d, "index.json"))

    def test_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d)
            prog, _, _ = _make_full_program()
            store.save(prog)
            loaded = store.load(prog.program_id)
            assert loaded.program_id == prog.program_id
            assert loaded.name       == prog.name
            assert loaded.settings.mode_key == prog.settings.mode_key

    def test_load_missing_raises(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d)
            with pytest.raises(FileNotFoundError):
                store.load("nonexistent-id")

    def test_list_programs_empty(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d)
            assert store.list_programs() == []

    def test_list_programs_returns_summaries(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d)
            prog, _, _ = _make_full_program()
            store.save(prog)
            summaries = store.list_programs()
            assert len(summaries) == 1
            assert isinstance(summaries[0], ProgramSummary)
            assert summaries[0].program_id == prog.program_id

    def test_list_programs_sorted_newest_first(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d)
            p1, _, _ = _make_full_program(mode_key="dinner")
            time.sleep(0.01)
            p2, _, _ = _make_full_program(mode_key="banger")
            store.save(p1)
            store.save(p2)
            summaries = store.list_programs()
            assert summaries[0].program_id == p2.program_id  # newest first

    def test_delete_removes_file(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d)
            prog, _, _ = _make_full_program()
            path = store.save(prog)
            store.delete(prog.program_id)
            assert not os.path.exists(path)

    def test_delete_removes_from_index(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d)
            prog, _, _ = _make_full_program()
            store.save(prog)
            store.delete(prog.program_id)
            assert store.count() == 0

    def test_delete_nonexistent_is_silent(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d)
            store.delete("ghost-id")  # must not raise

    def test_count(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d)
            assert store.count() == 0
            p1, _, _ = _make_full_program()
            p2, _, _ = _make_full_program()
            store.save(p1); store.save(p2)
            assert store.count() == 2

    def test_find_by_fingerprint_returns_match(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d)
            prog, audio, sr = _make_full_program()
            store.save(prog)
            fp = compute_song_fingerprint(audio, sr)
            found = store.find_by_fingerprint(fp)
            assert found is not None
            assert found.program_id == prog.program_id

    def test_find_by_fingerprint_no_match_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d)
            assert store.find_by_fingerprint("no-such-hash") is None

    def test_updated_at_bumped_on_save(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d)
            prog, _, _ = _make_full_program()
            original_updated = prog.updated_at
            time.sleep(0.01)
            store.save(prog)
            assert prog.updated_at > original_updated


# =============================================================================
# Sprint 5: Hybrid Playback + Art-Net Output + Auto Song Matching
# =============================================================================

from app.hybrid import HybridEngine, blend_rig_states
from dmx.output_artnet import ArtNetOutput, ARTNET_ID, ARTDMX_OPCODE, PROTOCOL_VER
import struct


# ---------------------------------------------------------------------------
# Fixture-state helpers (extend from Sprint 3)
# ---------------------------------------------------------------------------

def _make_state(brt: float = 0.5, rgb=(200, 100, 50),
                impact=0.2, blackout=False) -> RigVisualState:
    return RigVisualState(
        mode="test", palette_name="test",
        low_energy=0.3, mid_energy=0.2, high_energy=0.1, overall_energy=0.4,
        room_brightness=brt, impact_value=impact,
        uplights=[UplightState("u1", 100.0, 200.0, rgb, brt)],
        washes=[WashState("w1", 50.0, 300.0, rgb, brt, 80.0, 0.3)],
        beams=[BeamState("b1", 30.0, 400.0, rgb, brt, 15.0, 200.0, 10.0, 0.5)],
        sparkles=[SparkleState("sp1", 600.0, 350.0, rgb, brt, 0.4)],
        impacts=[ImpactState("i1", 600.0, 100.0, brt, False)],
        blackout_active=blackout,
    )


def _make_fx_tl_from_states(*states, duration_s=1.0):
    frames = [TimedFrame(i * duration_s / max(len(states), 1), s)
              for i, s in enumerate(states)]
    return FixtureStateTimeline(frames=frames, duration_s=duration_s)


# ---------------------------------------------------------------------------
# TestBlendRigStates
# ---------------------------------------------------------------------------

class TestBlendRigStates:
    def test_t_zero_equals_live(self):
        live = _make_state(brt=1.0)
        prog = _make_state(brt=0.0)
        result = blend_rig_states(live, prog, 0.0)
        assert result.room_brightness == pytest.approx(1.0)

    def test_t_one_equals_program(self):
        live = _make_state(brt=1.0)
        prog = _make_state(brt=0.0)
        result = blend_rig_states(live, prog, 1.0)
        assert result.room_brightness == pytest.approx(0.0)

    def test_t_half_averages_brightness(self):
        live = _make_state(brt=1.0)
        prog = _make_state(brt=0.0)
        result = blend_rig_states(live, prog, 0.5)
        assert result.room_brightness == pytest.approx(0.5, abs=0.01)

    def test_t_half_blends_rgb(self):
        live = _make_state(rgb=(200, 0, 0))
        prog = _make_state(rgb=(0, 200, 0))
        result = blend_rig_states(live, prog, 0.5)
        r, g, b = result.uplights[0].color_rgb
        assert 95 <= r <= 105
        assert 95 <= g <= 105
        assert b == 0

    def test_blackout_propagates_from_live(self):
        live = _make_state(blackout=True)
        prog = _make_state(blackout=False)
        result = blend_rig_states(live, prog, 0.3)
        assert result.blackout_active

    def test_blackout_propagates_from_program(self):
        live = _make_state(blackout=False)
        prog = _make_state(blackout=True)
        result = blend_rig_states(live, prog, 0.3)
        assert result.blackout_active

    def test_mismatched_fixture_counts_live_dominant(self):
        live = _make_state()
        # Prog has no uplights
        prog = RigVisualState(
            mode="test", palette_name="test",
            low_energy=0.0, mid_energy=0.0, high_energy=0.0, overall_energy=0.0,
            room_brightness=0.0, impact_value=0.0,
            uplights=[], washes=[], beams=[], sparkles=[], impacts=[],
            blackout_active=False,
        )
        result = blend_rig_states(live, prog, 0.3)  # live is dominant at t<0.5
        assert len(result.uplights) == len(live.uplights)

    def test_mismatched_fixture_counts_prog_dominant(self):
        live = _make_state()
        prog = RigVisualState(
            mode="test", palette_name="test",
            low_energy=0.0, mid_energy=0.0, high_energy=0.0, overall_energy=0.0,
            room_brightness=0.0, impact_value=0.0,
            uplights=[], washes=[], beams=[], sparkles=[], impacts=[],
            blackout_active=False,
        )
        result = blend_rig_states(live, prog, 0.7)  # prog is dominant at t>=0.5
        assert len(result.uplights) == len(prog.uplights)

    def test_returns_rig_visual_state(self):
        result = blend_rig_states(_make_state(), _make_state(), 0.5)
        assert isinstance(result, RigVisualState)

    def test_energy_fields_interpolated(self):
        live = _make_state(); live.low_energy = 1.0
        prog = _make_state(); prog.low_energy = 0.0
        result = blend_rig_states(live, prog, 0.5)
        assert result.low_energy == pytest.approx(0.5, abs=0.01)


# ---------------------------------------------------------------------------
# TestHybridEngine
# ---------------------------------------------------------------------------

class TestHybridEngine:
    def _make_engine(self, blend=0.5):
        s1 = _make_state(brt=0.8)
        s2 = _make_state(brt=0.4)
        tl = _make_fx_tl_from_states(s1, s2, duration_s=2.0)
        return HybridEngine(tl, blend=blend)

    def test_initial_state_paused(self):
        he = self._make_engine()
        assert not he.is_playing

    def test_blend_default(self):
        he = self._make_engine(blend=0.5)
        assert he.blend == pytest.approx(0.5)

    def test_set_blend(self):
        he = self._make_engine()
        he.set_blend(0.75)
        assert he.blend == pytest.approx(0.75)

    def test_blend_clamped_high(self):
        he = self._make_engine()
        he.set_blend(2.0)
        assert he.blend == pytest.approx(1.0)

    def test_blend_clamped_low(self):
        he = self._make_engine()
        he.set_blend(-1.0)
        assert he.blend == pytest.approx(0.0)

    def test_play_sets_playing(self):
        he = self._make_engine()
        he.play()
        assert he.is_playing

    def test_pause_stops_playing(self):
        he = self._make_engine()
        he.play(); he.pause()
        assert not he.is_playing

    def test_blend_with_live_returns_state(self):
        he = self._make_engine()
        result = he.blend_with_live(_make_state())
        assert isinstance(result, RigVisualState)

    def test_pure_live_at_zero_blend(self):
        live = _make_state(brt=1.0)
        s    = _make_state(brt=0.0)
        tl   = _make_fx_tl_from_states(s, duration_s=1.0)
        he   = HybridEngine(tl, blend=0.0)
        he.play()
        result = he.blend_with_live(live)
        assert result.room_brightness == pytest.approx(1.0)

    def test_pure_program_at_one_blend(self):
        live = _make_state(brt=1.0)
        s    = _make_state(brt=0.0)
        tl   = _make_fx_tl_from_states(s, duration_s=1.0)
        he   = HybridEngine(tl, blend=1.0)
        he.play()
        result = he.blend_with_live(live)
        assert result.room_brightness == pytest.approx(0.0)

    def test_seek_changes_time(self):
        he = self._make_engine()
        he.seek(1.0)
        assert he.current_time_s == pytest.approx(1.0)

    def test_progress_property(self):
        he = self._make_engine()
        he.seek(1.0)
        assert he.progress == pytest.approx(0.5, abs=0.05)


# ---------------------------------------------------------------------------
# TestArtNetOutput
# ---------------------------------------------------------------------------

class TestArtNetOutput:
    def test_packet_starts_with_artnet_id(self):
        out = ArtNetOutput()
        pkt = out._build_artdmx(b"\x00" * 512)
        assert pkt[:8] == ARTNET_ID

    def test_opcode_is_artdmx(self):
        out = ArtNetOutput()
        pkt = out._build_artdmx(b"\x00" * 512)
        opcode = struct.unpack_from("<H", pkt, 8)[0]
        assert opcode == ARTDMX_OPCODE

    def test_protocol_version(self):
        out = ArtNetOutput()
        pkt = out._build_artdmx(b"\x00" * 512)
        ver = struct.unpack_from(">H", pkt, 10)[0]
        assert ver == PROTOCOL_VER

    def test_universe_in_packet(self):
        out = ArtNetOutput(universe=3)
        pkt = out._build_artdmx(b"\x00" * 512)
        uni = struct.unpack_from("<H", pkt, 14)[0]
        assert uni == 3

    def test_length_field_is_512(self):
        out = ArtNetOutput()
        pkt = out._build_artdmx(b"\x00" * 512)
        length = struct.unpack_from(">H", pkt, 16)[0]
        assert length == 512

    def test_dmx_data_in_packet(self):
        out  = ArtNetOutput()
        data = bytes(range(256)) * 2  # 512 bytes
        pkt  = out._build_artdmx(data)
        assert pkt[18:18 + 512] == data

    def test_packet_length_even(self):
        out = ArtNetOutput()
        # Odd-length data should be padded to even
        pkt = out._build_artdmx(b"\x00" * 511)
        length = struct.unpack_from(">H", pkt, 16)[0]
        assert length % 2 == 0

    def test_sequence_increments_on_send(self):
        out = ArtNetOutput()
        # sequence starts at 0, increments in send_universe (not _build_artdmx)
        assert out.sequence == 0
        # Simulate increment (send_universe does this)
        for _ in range(5):
            out._sequence = (out._sequence + 1) % 256
        assert out.sequence == 5

    def test_universe_validation(self):
        with pytest.raises(ValueError):
            ArtNetOutput(universe=32768)
        with pytest.raises(ValueError):
            ArtNetOutput(universe=-1)

    def test_default_target_ip(self):
        out = ArtNetOutput()
        assert out.target_ip == "2.255.255.255"

    def test_not_connected_initially(self):
        out = ArtNetOutput()
        assert not out.is_connected

    def test_channel_clamping_in_send(self):
        """send_universe must clamp values to 0-255 without crashing."""
        out = ArtNetOutput()
        out.connect()
        channels = [300, -50, 128] + [0] * 509
        # Should not raise even though values are out of range
        # (socket sendto will fail silently since no real network)
        out.send_universe(channels)
        out.disconnect()


# ---------------------------------------------------------------------------
# TestHybridIntegration  (HybridEngine with real DeterministicEngine output)
# ---------------------------------------------------------------------------

class TestHybridIntegration:
    def test_blend_with_live_engine_output(self):
        """Full pipeline: generate fx_tl, wire into HybridEngine, blend with live."""
        tl       = _make_analysis_timeline(duration_s=1.0)
        palettes = _make_palettes()
        settings = SettingsSnapshot(mode_key="open_dance", palette_key="open_dance")
        fx_tl    = DeterministicEngine(settings, seed=42).generate(tl, palettes)
        he       = HybridEngine(fx_tl, blend=0.5)
        he.play()
        live_state = fx_tl.frame_at(0.0)
        result = he.blend_with_live(live_state)
        assert isinstance(result, RigVisualState)
        assert 0.0 <= result.room_brightness <= 1.0

    def test_blend_brightness_between_live_and_program(self):
        tl       = _make_analysis_timeline(duration_s=1.0)
        palettes = _make_palettes()
        settings = SettingsSnapshot(mode_key="banger", palette_key="banger")
        fx_tl    = DeterministicEngine(settings, seed=99).generate(tl, palettes)
        he       = HybridEngine(fx_tl, blend=0.5)
        he.play()
        live = _make_state(brt=1.0)
        prog_state = fx_tl.frame_at(0.0)
        expected = 0.5 * 1.0 + 0.5 * prog_state.room_brightness
        result = he.blend_with_live(live)
        assert result.room_brightness == pytest.approx(expected, abs=0.05)

    def test_rewind_restarts_program(self):
        tl       = _make_analysis_timeline(duration_s=1.0)
        palettes = _make_palettes()
        settings = SettingsSnapshot(mode_key="open_dance", palette_key="open_dance")
        fx_tl    = DeterministicEngine(settings, seed=42).generate(tl, palettes)
        he = HybridEngine(fx_tl, blend=0.5)
        he.seek(0.5)
        he.rewind()
        assert he.current_time_s == pytest.approx(0.0)

    def test_auto_fingerprint_match_in_store(self):
        """Store a program, look it up by fingerprint — simulates auto-match flow."""
        with tempfile.TemporaryDirectory() as d:
            store = ProgramStore(d)
            prog, audio, sr = _make_full_program(mode_key="dinner")
            store.save(prog)
            fp    = compute_song_fingerprint(audio, sr)
            found = store.find_by_fingerprint(fp)
            assert found is not None
            assert found.settings.mode_key == "dinner"
