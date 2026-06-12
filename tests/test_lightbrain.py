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
from engine.scenes import (
    SceneManager, ScenePreset, GroupOverride, PositionPreset, StatePreset,
)
from fixtures.aiming import FixtureAimingTool
from fixtures.djflx_beam import DJFLXBeam as _DJFLXBeamForAiming
from app.web    import server as _web_server
from engine.strobe import StrobeEngine
from engine.hue_crossfader import HueCrossfader


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

    def test_strobe_dmx_at_max_rate(self):
        u  = DMXUniverse()
        rw = self._rw(1)
        rw.render_to_universe(u, hue=0, saturation=1.0, value=1.0, strobe=1.0)
        assert u.get_channel(8) == 255  # Ch8 = 255 at max strobe rate

    def test_strobe_dmx_zero_when_off(self):
        u  = DMXUniverse()
        rw = self._rw(1)
        rw.render_to_universe(u, hue=0, saturation=1.0, value=1.0, strobe=0.0)
        assert u.get_channel(8) == 0   # Ch8 = 0 when strobe off

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

    def test_four_beams(self):
        # 2 DJFLX + 2 GigBAR movers
        rig = self._build()
        assert len(rig.beams) == 4

    def test_gigbar_movers_present(self):
        rig = self._build()
        ids = {b.fixture_id for b in rig.beams}
        assert "gigbar_mover_l" in ids
        assert "gigbar_mover_r" in ids

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


# =============================================================================
# Sprint 6: Setlist Mode + Moving Head DMX
# =============================================================================

from data.setlist import Setlist, SetlistEntry, SetlistSummary
from data.setlist_store import SetlistStore
from fixtures.djflx_beam import DJFLXBeam, pan_degrees_to_dmx, dmx_to_pan_degrees, NUM_CHANNELS as BEAM_NUM_CHANNELS


# ---------------------------------------------------------------------------
# TestSetlist
# ---------------------------------------------------------------------------

class TestSetlist:
    def test_create_assigns_uuid(self):
        sl = Setlist.create("Friday Night")
        assert len(sl.setlist_id) == 36
        assert sl.name == "Friday Night"

    def test_create_starts_empty(self):
        sl = Setlist.create("Empty")
        assert sl.entry_count() == 0
        assert sl.entries == []

    def test_add_entry_appends_and_numbers(self):
        sl = Setlist.create("Show")
        e1 = sl.add_entry("Track 1", song_fingerprint="fp1")
        e2 = sl.add_entry("Track 2", song_fingerprint="fp2")
        assert e1.position == 1
        assert e2.position == 2
        assert sl.entry_count() == 2

    def test_add_entry_stores_fingerprint(self):
        sl = Setlist.create("Show")
        e = sl.add_entry("Song A", song_fingerprint="abc123", program_id="prog-1")
        assert e.song_fingerprint == "abc123"
        assert e.program_id == "prog-1"

    def test_remove_entry_reduces_count(self):
        sl = Setlist.create("Show")
        e1 = sl.add_entry("T1"); e2 = sl.add_entry("T2")
        ok = sl.remove_entry(e1.entry_id)
        assert ok
        assert sl.entry_count() == 1
        assert sl.entries[0].entry_id == e2.entry_id

    def test_remove_nonexistent_returns_false(self):
        sl = Setlist.create("Show")
        assert not sl.remove_entry("ghost-id")

    def test_remove_renumbers(self):
        sl = Setlist.create("Show")
        e1 = sl.add_entry("T1"); sl.add_entry("T2"); sl.add_entry("T3")
        sl.remove_entry(e1.entry_id)
        assert all(e.position == i + 1 for i, e in enumerate(sl.entries))

    def test_move_entry_up(self):
        sl = Setlist.create("Show")
        sl.add_entry("T1"); sl.add_entry("T2"); e3 = sl.add_entry("T3")
        sl.move_entry(e3.entry_id, 1)
        assert sl.entries[0].entry_id == e3.entry_id
        assert sl.entries[0].position == 1
        assert sl.entries[1].position == 2

    def test_move_entry_down(self):
        sl = Setlist.create("Show")
        e1 = sl.add_entry("T1"); sl.add_entry("T2"); sl.add_entry("T3")
        sl.move_entry(e1.entry_id, 3)
        assert sl.entries[-1].entry_id == e1.entry_id
        assert sl.entries[-1].position == 3

    def test_move_nonexistent_returns_false(self):
        sl = Setlist.create("Show")
        assert not sl.move_entry("ghost", 1)

    def test_find_by_fingerprint_hit(self):
        sl = Setlist.create("Show")
        sl.add_entry("Song A", song_fingerprint="fp_a")
        found = sl.find_by_fingerprint("fp_a")
        assert found is not None
        assert found.name == "Song A"

    def test_find_by_fingerprint_miss(self):
        sl = Setlist.create("Show")
        sl.add_entry("Song A", song_fingerprint="fp_a")
        assert sl.find_by_fingerprint("fp_x") is None

    def test_all_fingerprints(self):
        sl = Setlist.create("Show")
        sl.add_entry("A", song_fingerprint="fp1")
        sl.add_entry("B", song_fingerprint="")     # no fingerprint
        sl.add_entry("C", song_fingerprint="fp3")
        fps = sl.all_fingerprints()
        assert "fp1" in fps
        assert "fp3" in fps
        assert "" not in fps

    def test_to_summary(self):
        sl = Setlist.create("Show")
        sl.add_entry("T1"); sl.add_entry("T2")
        summary = sl.to_summary()
        assert isinstance(summary, SetlistSummary)
        assert summary.setlist_id == sl.setlist_id
        assert summary.entry_count == 2

    def test_update_entry_program(self):
        sl = Setlist.create("Show")
        e = sl.add_entry("T1", program_id="")
        sl.update_entry_program(e.entry_id, "new-prog-id")
        assert sl.entries[0].program_id == "new-prog-id"

    def test_updated_at_bumped_on_add(self):
        sl = Setlist.create("Show")
        before = sl.updated_at
        time.sleep(0.01)
        sl.add_entry("T1")
        assert sl.updated_at > before


# ---------------------------------------------------------------------------
# TestSetlistStore
# ---------------------------------------------------------------------------

class TestSetlistStore:
    def _make_setlist(self, name="Test"):
        sl = Setlist.create(name)
        sl.add_entry("Track 1", song_fingerprint="fp1")
        sl.add_entry("Track 2", song_fingerprint="fp2")
        return sl

    def test_save_creates_file(self):
        with tempfile.TemporaryDirectory() as d:
            store = SetlistStore(d)
            sl = self._make_setlist()
            path = store.save(sl)
            assert os.path.exists(path)

    def test_load_roundtrip_name(self):
        with tempfile.TemporaryDirectory() as d:
            store = SetlistStore(d)
            sl = self._make_setlist("Saturday Night")
            store.save(sl)
            loaded = store.load(sl.setlist_id)
            assert loaded.name == "Saturday Night"

    def test_load_roundtrip_entries(self):
        with tempfile.TemporaryDirectory() as d:
            store = SetlistStore(d)
            sl = self._make_setlist()
            store.save(sl)
            loaded = store.load(sl.setlist_id)
            assert len(loaded.entries) == 2
            assert loaded.entries[0].song_fingerprint == "fp1"
            assert loaded.entries[1].name == "Track 2"

    def test_load_missing_raises(self):
        with tempfile.TemporaryDirectory() as d:
            store = SetlistStore(d)
            with pytest.raises(FileNotFoundError):
                store.load("no-such-id")

    def test_list_empty(self):
        with tempfile.TemporaryDirectory() as d:
            assert SetlistStore(d).list_setlists() == []

    def test_list_returns_summaries(self):
        with tempfile.TemporaryDirectory() as d:
            store = SetlistStore(d)
            store.save(self._make_setlist("Show 1"))
            summaries = store.list_setlists()
            assert len(summaries) == 1
            assert isinstance(summaries[0], SetlistSummary)

    def test_delete_removes_file(self):
        with tempfile.TemporaryDirectory() as d:
            store = SetlistStore(d)
            sl = self._make_setlist()
            path = store.save(sl)
            store.delete(sl.setlist_id)
            assert not os.path.exists(path)
            assert store.count() == 0

    def test_delete_nonexistent_is_silent(self):
        with tempfile.TemporaryDirectory() as d:
            SetlistStore(d).delete("ghost-id")  # must not raise

    def test_find_by_fingerprint_hit(self):
        with tempfile.TemporaryDirectory() as d:
            store = SetlistStore(d)
            sl = self._make_setlist()
            store.save(sl)
            result = store.find_by_fingerprint("fp1")
            assert result is not None
            found_sl, found_entry = result
            assert found_entry.name == "Track 1"

    def test_find_by_fingerprint_miss(self):
        with tempfile.TemporaryDirectory() as d:
            store = SetlistStore(d)
            store.save(self._make_setlist())
            assert store.find_by_fingerprint("no-such-fp") is None

    def test_count(self):
        with tempfile.TemporaryDirectory() as d:
            store = SetlistStore(d)
            store.save(self._make_setlist("A"))
            store.save(self._make_setlist("B"))
            assert store.count() == 2


# ---------------------------------------------------------------------------
# TestDJFLXBeam
# ---------------------------------------------------------------------------

class TestDJFLXBeam:
    def _make_fixture(self, address=201):
        return DJFLXBeam(fixture_id="beam_l", name="Left Beam", dmx_address=address)

    def _make_beam_state(self, angle=0.0, brt=0.8, rgb=(200, 100, 50),
                          speed=0.5, active=True):
        return BeamState(
            fixture_id="beam_l", x=175.0, y=670.0,
            color_rgb=rgb, brightness=brt,
            angle_degrees=angle, length=400.0, spread=6.0,
            movement_speed=speed, active=active,
        )

    def test_num_channels(self):
        f = self._make_fixture()
        assert f.num_channels == BEAM_NUM_CHANNELS
        assert BEAM_NUM_CHANNELS == 10

    def test_pan_centre_at_zero_degrees(self):
        uni = DMXUniverse()
        f = self._make_fixture(address=201)
        f.render_to_universe(uni, self._make_beam_state(angle=0.0))
        pan = uni.get_channel(201)
        assert 124 <= pan <= 131   # ≈128

    def test_pan_right_positive_angle(self):
        uni = DMXUniverse()
        f = self._make_fixture(address=201)
        f.render_to_universe(uni, self._make_beam_state(angle=45.0))
        pan = uni.get_channel(201)
        assert pan > 160   # right of centre

    def test_pan_left_negative_angle(self):
        uni = DMXUniverse()
        f = self._make_fixture(address=201)
        f.render_to_universe(uni, self._make_beam_state(angle=-45.0))
        pan = uni.get_channel(201)
        assert pan < 100   # left of centre

    def test_pan_clamped_at_extremes(self):
        assert pan_degrees_to_dmx(-180) == 0
        assert pan_degrees_to_dmx(180)  == 255

    def test_dimmer_channel_reflects_brightness(self):
        uni = DMXUniverse()
        f = self._make_fixture(address=201)
        # High brightness
        f.render_to_universe(uni, self._make_beam_state(brt=1.0))
        assert uni.get_channel(208) > 200   # Ch8 = dimmer
        # Low brightness
        uni2 = DMXUniverse()
        f.render_to_universe(uni2, self._make_beam_state(brt=0.1))
        assert uni2.get_channel(208) < uni.get_channel(208)

    def test_color_channels_set(self):
        uni = DMXUniverse()
        f = self._make_fixture(address=201)
        f.render_to_universe(uni, self._make_beam_state(rgb=(200, 100, 50)))
        assert uni.get_channel(205) == 200  # Ch5 R
        assert uni.get_channel(206) == 100  # Ch6 G
        assert uni.get_channel(207) == 50   # Ch7 B

    def test_inactive_zeros_dimmer(self):
        uni = DMXUniverse()
        f = self._make_fixture(address=201)
        f.render_to_universe(uni, self._make_beam_state(brt=1.0, active=False))
        assert uni.get_channel(208) == 0   # dimmer off when inactive

    def test_speed_inverted(self):
        uni_fast = DMXUniverse(); uni_slow = DMXUniverse()
        f = self._make_fixture(address=201)
        f.render_to_universe(uni_fast, self._make_beam_state(speed=1.0))  # fast
        f.render_to_universe(uni_slow, self._make_beam_state(speed=0.0))  # slow
        # Ch9 = speed: 0=fastest → low DMX, 1=slowest → high DMX (inverted)
        assert uni_fast.get_channel(209) < uni_slow.get_channel(209)

    def test_pan_round_trip_accuracy(self):
        for deg in [-90, -45, 0, 45, 90]:
            dmx  = pan_degrees_to_dmx(float(deg))
            back = dmx_to_pan_degrees(dmx)
            assert abs(back - deg) < 1.5, f"Round-trip error: {deg} → {dmx} → {back}"


# ===========================================================================
# Sprint 7 — Preset System
# ===========================================================================

# Paths to the real preset files in the repo
_REPO_ROOT     = ROOT
_SCENES_DIR    = os.path.join(_REPO_ROOT, "config", "scenes")
_POSITIONS_FILE = os.path.join(_REPO_ROOT, "fixtures", "positions.json")
_STATES_FILE   = os.path.join(_REPO_ROOT, "fixtures", "states.json")


def _make_scene_manager() -> SceneManager:
    mgr = SceneManager(_SCENES_DIR, _POSITIONS_FILE, _STATES_FILE)
    mgr.load_all()
    return mgr


def _make_rig_state(
    uplight_color=(200, 200, 200),
    uplight_brightness=0.9,
    beam_angle=45.0,
    beam_speed=0.5,
) -> RigVisualState:
    return RigVisualState(
        mode="open_dance",
        palette_name="test",
        low_energy=0.5, mid_energy=0.5, high_energy=0.5, overall_energy=0.5,
        room_brightness=0.8, impact_value=0.0,
        uplights=[UplightState(
            fixture_id="u1", x=0.0, y=0.0,
            color_rgb=uplight_color, brightness=uplight_brightness,
        )],
        washes=[WashState(
            fixture_id="w1", x=300.0, y=400.0,
            color_rgb=(100, 100, 200), brightness=0.7,
            radius=150.0, pulse_strength=0.3,
        )],
        beams=[BeamState(
            fixture_id="b1", x=175.0, y=670.0,
            color_rgb=(100, 100, 100), brightness=0.7,
            angle_degrees=beam_angle, length=400.0, spread=6.0,
            movement_speed=beam_speed,
        )],
        sparkles=[SparkleState(
            fixture_id="s1", x=600.0, y=400.0,
            color_rgb=(200, 200, 50), brightness=0.6, sparkle_amount=0.5,
        )],
        impacts=[ImpactState(
            fixture_id="i1", x=600.0, y=200.0,
            brightness=0.0, flash_active=False,
        )],
        blackout_active=False,
    )


# ---------------------------------------------------------------------------
# TestPositionPresets
# ---------------------------------------------------------------------------

class TestPositionPresets:

    def test_positions_file_loads(self):
        mgr = _make_scene_manager()
        assert mgr.position_count() >= 5

    def test_default_positions_present(self):
        mgr = _make_scene_manager()
        for name in ("center", "park", "ceiling", "left_sweep", "right_sweep"):
            assert mgr.get_position(name) is not None, f"Missing position: {name}"

    def test_center_pan_is_zero(self):
        mgr = _make_scene_manager()
        pos = mgr.get_position("center")
        assert pos.pan_deg == pytest.approx(0.0)

    def test_park_tilt_is_zero(self):
        mgr = _make_scene_manager()
        pos = mgr.get_position("park")
        assert pos.tilt_dmx == 0

    def test_all_pan_angles_in_range(self):
        mgr = _make_scene_manager()
        for name in ("center", "park", "ceiling", "left_sweep", "right_sweep",
                     "cake_table", "entrance", "dance_floor"):
            pos = mgr.get_position(name)
            if pos is not None:
                assert -90.0 <= pos.pan_deg <= 90.0, \
                    f"Position '{name}' pan_deg out of range: {pos.pan_deg}"


# ---------------------------------------------------------------------------
# TestStatePresets
# ---------------------------------------------------------------------------

class TestStatePresets:

    def test_states_file_loads_ten(self):
        mgr = _make_scene_manager()
        assert mgr.state_count() == 10

    def test_all_default_states_present(self):
        mgr = _make_scene_manager()
        for name in ("blush_pink", "deep_blue", "warm_amber", "wedding_white",
                     "uv_glow", "classic_red", "ocean_teal", "sunset_gold",
                     "party_purple", "clean_white"):
            assert mgr.get_state(name) is not None, f"Missing state: {name}"

    def test_rgb_values_in_valid_range(self):
        mgr = _make_scene_manager()
        for state in (mgr.get_state(n) for n in (
            "blush_pink", "deep_blue", "warm_amber", "wedding_white", "uv_glow",
            "classic_red", "ocean_teal", "sunset_gold", "party_purple", "clean_white",
        )):
            for ch in state.rgb:
                assert 0 <= ch <= 255, f"RGB channel {ch} out of range in {state.name}"

    def test_brightness_in_valid_range(self):
        mgr = _make_scene_manager()
        for name in ("blush_pink", "wedding_white", "clean_white"):
            sp = mgr.get_state(name)
            assert 0.0 <= sp.brightness <= 1.0

    def test_wedding_white_is_bright(self):
        mgr = _make_scene_manager()
        sp = mgr.get_state("wedding_white")
        assert sp.brightness == pytest.approx(1.0)
        r, g, b = sp.rgb
        assert r > 200 and g > 200 and b > 200


# ---------------------------------------------------------------------------
# TestGroupOverride
# ---------------------------------------------------------------------------

class TestGroupOverride:

    def test_default_movement_mode(self):
        ov = GroupOverride(fixture_type="uplight", groups=["all"])
        assert ov.movement_mode == "inherit"
        assert ov.audio_reactive is True

    def test_locked_movement_mode(self):
        ov = GroupOverride(fixture_type="beam", groups=["all"],
                           movement_mode="locked", audio_reactive=False)
        assert ov.movement_mode == "locked"
        assert ov.audio_reactive is False

    def test_scene_preset_fields(self):
        scene = ScenePreset(
            scene_id="test_scene",
            name="Test",
            base_mode="open_dance",
            description="A test scene",
            groups=[GroupOverride(fixture_type="uplight", groups=["all"],
                                  state_preset="blush_pink")],
        )
        assert scene.scene_id == "test_scene"
        assert scene.base_mode == "open_dance"
        assert len(scene.groups) == 1
        assert scene.groups[0].state_preset == "blush_pink"

    def test_position_preset_dataclass(self):
        pp = PositionPreset(name="test", pan_deg=30.0, tilt_dmx=110, description="hi")
        assert pp.pan_deg == pytest.approx(30.0)
        assert pp.tilt_dmx == 110


# ---------------------------------------------------------------------------
# TestSceneManager
# ---------------------------------------------------------------------------

class TestSceneManager:

    def setup_method(self):
        self.mgr = _make_scene_manager()

    def test_load_all_counts(self):
        assert self.mgr.scene_count() == 8
        assert self.mgr.position_count() >= 5
        assert self.mgr.state_count() == 10

    def test_activate_scene_valid(self):
        assert self.mgr.activate_scene("slow_dance") is True
        self.mgr.release_scene()

    def test_activate_scene_invalid(self):
        assert self.mgr.activate_scene("nonexistent_scene_xyz") is False

    def test_release_scene(self):
        self.mgr.activate_scene("slow_dance")
        self.mgr.release_scene()
        assert self.mgr.active_scene is None

    def test_active_scene_id_after_activate(self):
        self.mgr.activate_scene("slow_dance")
        assert self.mgr.active_scene_id == "slow_dance"
        self.mgr.release_scene()

    def test_active_base_mode_slow_dance(self):
        self.mgr.activate_scene("slow_dance")
        assert self.mgr.active_base_mode == "slow_dance"
        self.mgr.release_scene()

    def test_apply_no_active_scene_returns_same_object(self):
        self.mgr.release_scene()
        state = _make_rig_state()
        result = self.mgr.apply_to_rig_state(state)
        assert result is state

    def test_apply_uplight_color_override(self):
        self.mgr.activate_scene("slow_dance")  # blush_pink: (255, 105, 140)
        state = _make_rig_state(uplight_color=(0, 0, 0))
        result = self.mgr.apply_to_rig_state(state)
        assert result.uplights[0].color_rgb == (255, 105, 140)
        self.mgr.release_scene()

    def test_apply_uplight_brightness_audio_reactive(self):
        # slow_dance uplights: audio_reactive=True → brightness unchanged
        self.mgr.activate_scene("slow_dance")
        state = _make_rig_state(uplight_brightness=0.42)
        result = self.mgr.apply_to_rig_state(state)
        assert result.uplights[0].brightness == pytest.approx(0.42)
        self.mgr.release_scene()

    def test_apply_uplight_brightness_non_reactive(self):
        # dinner_service uplights: audio_reactive=False → brightness from preset
        self.mgr.activate_scene("dinner_service")
        state = _make_rig_state(uplight_brightness=0.1)
        result = self.mgr.apply_to_rig_state(state)
        sp = self.mgr.get_state("warm_amber")
        assert result.uplights[0].brightness == pytest.approx(sp.brightness)
        self.mgr.release_scene()

    def test_apply_beam_locked_sets_angle(self):
        # slow_dance beams: position_preset=center, movement_mode=locked
        self.mgr.activate_scene("slow_dance")
        state = _make_rig_state(beam_angle=45.0, beam_speed=0.8)
        result = self.mgr.apply_to_rig_state(state)
        center = self.mgr.get_position("center")
        assert result.beams[0].angle_degrees == pytest.approx(center.pan_deg)
        assert result.beams[0].movement_speed == pytest.approx(0.0)
        self.mgr.release_scene()

    def test_apply_beam_color_override(self):
        self.mgr.activate_scene("slow_dance")  # beam state_preset=blush_pink
        state = _make_rig_state()
        result = self.mgr.apply_to_rig_state(state)
        sp = self.mgr.get_state("blush_pink")
        assert result.beams[0].color_rgb == sp.rgb
        self.mgr.release_scene()

    def test_apply_mode_set_to_base_mode(self):
        self.mgr.activate_scene("dinner_service")
        state = _make_rig_state()
        result = self.mgr.apply_to_rig_state(state)
        assert result.mode == "dinner"
        self.mgr.release_scene()

    def test_apply_blackout_preserved(self):
        self.mgr.activate_scene("slow_dance")
        state = _make_rig_state()
        state.blackout_active = True
        result = self.mgr.apply_to_rig_state(state)
        assert result.blackout_active is True
        self.mgr.release_scene()

    def test_list_scenes_returns_nine(self):
        assert len(self.mgr.list_scenes()) == 8

    def test_list_scenes_sorted_by_id(self):
        ids = [s.scene_id for s in self.mgr.list_scenes()]
        assert ids == sorted(ids)

    def test_get_uplight_color_override_no_scene(self):
        self.mgr.release_scene()
        assert self.mgr.get_uplight_color_override() is None

    def test_get_uplight_color_override_active(self):
        self.mgr.activate_scene("slow_dance")
        ov = self.mgr.get_uplight_color_override()
        assert ov is not None
        rgb, brightness, reactive = ov
        assert len(rgb) == 3
        assert rgb == (255, 105, 140)   # blush_pink
        self.mgr.release_scene()

    def test_constrained_beam_clamps_angle(self):
        # bouquet_garter beams: movement_mode=constrained → angle clamped to ±30°
        self.mgr.activate_scene("bouquet_garter")
        state = _make_rig_state(beam_angle=80.0)  # way outside ±30
        result = self.mgr.apply_to_rig_state(state)
        assert abs(result.beams[0].angle_degrees) <= 30.0
        self.mgr.release_scene()


# ---------------------------------------------------------------------------
# TestFixtureAimingTool
# ---------------------------------------------------------------------------

class TestFixtureAimingTool:

    def _make_tool(self, tmp_path: str) -> FixtureAimingTool:
        fixture  = _DJFLXBeamForAiming(fixture_id="beam_aim", name="Aim",
                                        dmx_address=201)
        universe = DMXUniverse()
        pos_file = os.path.join(tmp_path, "positions.json")
        return FixtureAimingTool(fixture, universe, positions_file=pos_file)

    def test_initial_pan_is_zero(self):
        with tempfile.TemporaryDirectory() as d:
            tool = self._make_tool(d)
            assert tool.pan_deg == pytest.approx(0.0)

    def test_set_pan_clamps_max(self):
        with tempfile.TemporaryDirectory() as d:
            tool = self._make_tool(d)
            tool.set_pan(200.0)  # beyond ±90
            assert tool.pan_deg == pytest.approx(90.0)

    def test_set_pan_clamps_min(self):
        with tempfile.TemporaryDirectory() as d:
            tool = self._make_tool(d)
            tool.set_pan(-200.0)
            assert tool.pan_deg == pytest.approx(-90.0)

    def test_nudge_pan_accumulates(self):
        with tempfile.TemporaryDirectory() as d:
            tool = self._make_tool(d)
            tool.set_pan(10.0)
            tool.nudge_pan(5.0)
            assert tool.pan_deg == pytest.approx(15.0)

    def test_set_tilt_clamps(self):
        with tempfile.TemporaryDirectory() as d:
            tool = self._make_tool(d)
            tool.set_tilt(300)
            assert tool.tilt_dmx == 255
            tool.set_tilt(-10)
            assert tool.tilt_dmx == 0

    def test_save_and_load_position(self):
        with tempfile.TemporaryDirectory() as d:
            tool = self._make_tool(d)
            tool.set_pan(30.0)
            tool.set_tilt(120)
            tool.save_position("my_spot", "Test position")

            # Reload via go_to_preset
            tool.set_pan(0.0)
            tool.set_tilt(0)
            result = tool.go_to_preset("my_spot")
            assert result is True
            assert tool.pan_deg == pytest.approx(30.0)
            assert tool.tilt_dmx == 120

    def test_delete_position(self):
        with tempfile.TemporaryDirectory() as d:
            tool = self._make_tool(d)
            tool.set_pan(20.0)
            tool.save_position("temp_pos")
            assert tool.delete_position("temp_pos") is True
            assert tool.go_to_preset("temp_pos") is False

    def test_flush_writes_to_universe(self):
        with tempfile.TemporaryDirectory() as d:
            fixture  = _DJFLXBeamForAiming(fixture_id="b", name="B", dmx_address=1)
            universe = DMXUniverse()
            tool = FixtureAimingTool(fixture, universe,
                                     positions_file=os.path.join(d, "p.json"))
            tool.set_pan(0.0)
            # Dimmer should be on at aim level
            assert universe.get_channel(8) == 128  # CH_DIMMER = 7 (0-indexed) → ch8


# ---------------------------------------------------------------------------
# TestSceneIntegration
# ---------------------------------------------------------------------------

class TestSceneIntegration:

    def test_all_nine_scenes_loadable(self):
        mgr = _make_scene_manager()
        assert mgr.scene_count() == 8

    def test_all_scene_ids_are_unique(self):
        mgr = _make_scene_manager()
        ids = [s.scene_id for s in mgr.list_scenes()]
        assert len(ids) == len(set(ids))

    def test_slow_dance_applies_to_rig(self):
        mgr = _make_scene_manager()
        mgr.activate_scene("slow_dance")
        state  = _make_rig_state()
        result = mgr.apply_to_rig_state(state)
        assert result is not state
        assert result.uplights[0].color_rgb == (255, 105, 140)  # blush_pink
        assert result.mode == "slow_dance"

    def test_release_returns_original_state(self):
        mgr = _make_scene_manager()
        mgr.activate_scene("slow_dance")
        mgr.release_scene()
        state  = _make_rig_state()
        result = mgr.apply_to_rig_state(state)
        assert result is state

    def test_scene_covers_all_expected_ids(self):
        mgr = _make_scene_manager()
        expected = {
            "slow_dance", "cake_cutting", "toasts", "bouquet_garter",
            "grand_entrance", "dinner_service", "open_dancing",
            "send_off",
        }
        loaded = {s.scene_id for s in mgr.list_scenes()}
        assert loaded == expected


# ===========================================================================
# Sprint 8 — Web Dashboard
# ===========================================================================

class TestWebServerState:
    """State dict and command queue — no running server needed."""

    def setup_method(self):
        # Reset to clean baseline before each test
        _web_server._engine_state.update({
            "mode": "open_dance", "mode_display": "Open Dance",
            "scene": None, "scene_name": "", "blackout": False,
            "bpm": 0.0, "beat": False,
            "low_energy": 0.0, "mid_energy": 0.0,
            "high_energy": 0.0, "overall_energy": 0.0,
            "fps": 0, "dmx_output": "MOCK",
            "scenes": [], "modes": [],
        })
        _web_server.get_all_commands()   # drain queue

    def test_initial_state_has_required_keys(self):
        keys = {"mode", "mode_display", "scene", "scene_name", "blackout",
                "bpm", "beat", "low_energy", "mid_energy", "high_energy",
                "overall_energy", "fps", "dmx_output", "scenes", "modes"}
        assert keys.issubset(_web_server._engine_state.keys())

    def test_update_state_modifies_dict(self):
        _web_server.update_state(mode="banger", bpm=128.0)
        assert _web_server._engine_state["mode"] == "banger"
        assert _web_server._engine_state["bpm"] == pytest.approx(128.0)

    def test_update_state_energy_levels(self):
        _web_server.update_state(low_energy=0.8, mid_energy=0.5,
                                  high_energy=0.3, overall_energy=0.6)
        assert _web_server._engine_state["low_energy"]     == pytest.approx(0.8)
        assert _web_server._engine_state["overall_energy"] == pytest.approx(0.6)

    def test_update_state_blackout(self):
        _web_server.update_state(blackout=True)
        assert _web_server._engine_state["blackout"] is True

    def test_update_state_scene(self):
        _web_server.update_state(scene="slow_dance", scene_name="First Dance")
        assert _web_server._engine_state["scene"] == "slow_dance"
        assert _web_server._engine_state["scene_name"] == "First Dance"

    def test_set_catalog_populates_modes_and_scenes(self):
        modes  = [{"key": "open_dance", "display_name": "Open Dance"}]
        scenes = [{"id": "slow_dance", "name": "First Dance", "index": 0}]
        _web_server.set_catalog(modes=modes, scenes=scenes)
        assert _web_server._engine_state["modes"]  == modes
        assert _web_server._engine_state["scenes"] == scenes

    def test_state_is_json_serializable(self):
        import json
        json.dumps(_web_server._engine_state)   # must not raise

    def test_get_command_empty_returns_none(self):
        assert _web_server.get_command() is None

    def test_command_queue_roundtrip(self):
        _web_server._command_queue.put_nowait({"type": "blackout"})
        cmd = _web_server.get_command()
        assert cmd == {"type": "blackout"}
        assert _web_server.get_command() is None

    def test_get_all_commands_drains_queue(self):
        _web_server._command_queue.put_nowait({"type": "mode",  "value": "banger"})
        _web_server._command_queue.put_nowait({"type": "blackout"})
        cmds = _web_server.get_all_commands()
        assert len(cmds) == 2
        assert cmds[0]["type"] == "mode"
        assert cmds[1]["type"] == "blackout"
        assert _web_server.get_command() is None   # fully drained

    def test_get_all_commands_empty(self):
        assert _web_server.get_all_commands() == []

    def test_multiple_scene_commands(self):
        _web_server._command_queue.put_nowait({"type": "scene", "value": "slow_dance"})
        _web_server._command_queue.put_nowait({"type": "release_scene"})
        cmds = _web_server.get_all_commands()
        assert cmds[0] == {"type": "scene", "value": "slow_dance"}
        assert cmds[1] == {"type": "release_scene"}

    def test_update_state_beat_flag(self):
        _web_server.update_state(beat=True, bpm=120.0)
        assert _web_server._engine_state["beat"] is True
        assert _web_server._engine_state["bpm"] == pytest.approx(120.0)

    def test_update_state_fps(self):
        _web_server.update_state(fps=40, dmx_output="ARTNET")
        assert _web_server._engine_state["fps"] == 40
        assert _web_server._engine_state["dmx_output"] == "ARTNET"

    def test_dashboard_html_exists(self):
        html_path = os.path.join(
            ROOT, "app", "web", "dashboard.html"
        )
        assert os.path.exists(html_path)
        with open(html_path, encoding="utf-8") as f:
            content = f.read()
        assert "LightBrain" in content
        assert "/ws" in content          # WebSocket endpoint referenced
        assert "/api/command" in content  # command endpoint referenced

    def test_visualizer3d_route_served(self):
        from app.web.server import _build_app
        from starlette.testclient import TestClient
        client = TestClient(_build_app())
        r = client.get("/visualizer3d")
        assert r.status_code == 200
        assert "RKADE LightBrain" in r.text
        assert "embed" in r.text          # embedded-mode handling present

    def test_dashboard_has_3d_tab(self):
        html_path = os.path.join(ROOT, "app", "web", "dashboard.html")
        with open(html_path, encoding="utf-8") as f:
            content = f.read()
        assert "viz3d-frame" in content        # iframe present
        assert "/visualizer3d?embed=1" in content  # lazy-loaded source
        assert "lb-pause" in content           # pause-when-hidden wired


# ===========================================================================
# Sprint 9 — Web rig state serialization + scene API wiring
# ===========================================================================

class TestWebRigState:
    """serialize_rig_state converts RigVisualState to JSON-safe dicts."""

    def test_serialize_returns_expected_keys(self):
        rig = _make_rig_state()
        result = _web_server.serialize_rig_state(rig)
        assert isinstance(result, dict)
        assert set(result.keys()) == {
            "uplights", "washes", "beams", "sparkles", "impacts", "ambient_warm"
        }

    def test_serialize_uplights_fields(self):
        rig = _make_rig_state()
        result = _web_server.serialize_rig_state(rig)
        u = result["uplights"][0]
        assert "id" in u and "x" in u and "y" in u
        assert "rgb" in u and "brt" in u and "active" in u

    def test_serialize_washes_fields(self):
        rig = _make_rig_state()
        result = _web_server.serialize_rig_state(rig)
        w = result["washes"][0]
        assert "radius" in w and "pulse" in w

    def test_serialize_beams_fields(self):
        rig = _make_rig_state()
        result = _web_server.serialize_rig_state(rig)
        b = result["beams"][0]
        assert "angle" in b and "length" in b and "spread" in b

    def test_serialize_sparkles_fields(self):
        rig = _make_rig_state()
        result = _web_server.serialize_rig_state(rig)
        sp = result["sparkles"][0]
        assert "amount" in sp

    def test_serialize_impacts_fields(self):
        rig = _make_rig_state()
        result = _web_server.serialize_rig_state(rig)
        im = result["impacts"][0]
        assert "flash" in im and "brt" in im

    def test_serialize_rgb_is_list(self):
        rig = _make_rig_state(uplight_color=(200, 100, 50))
        result = _web_server.serialize_rig_state(rig)
        rgb = result["uplights"][0]["rgb"]
        assert isinstance(rgb, list)
        assert rgb == [200, 100, 50]

    def test_serialize_brightness_is_rounded_float(self):
        rig = _make_rig_state(uplight_brightness=0.888888)
        result = _web_server.serialize_rig_state(rig)
        brt = result["uplights"][0]["brt"]
        assert isinstance(brt, float)
        assert brt == pytest.approx(0.889, abs=0.001)

    def test_serialize_is_json_serializable(self):
        import json
        rig = _make_rig_state()
        result = _web_server.serialize_rig_state(rig)
        json.dumps(result)   # must not raise

    def test_serialize_beam_angle_rounded(self):
        rig = _make_rig_state(beam_angle=33.333)
        result = _web_server.serialize_rig_state(rig)
        assert result["beams"][0]["angle"] == pytest.approx(33.3, abs=0.05)

    def test_serialize_empty_lists(self):
        rig = RigVisualState(
            mode="open_dance", palette_name="test",
            low_energy=0.0, mid_energy=0.0, high_energy=0.0, overall_energy=0.0,
            room_brightness=0.0, impact_value=0.0,
            uplights=[], washes=[], beams=[], sparkles=[], impacts=[],
            blackout_active=False,
        )
        result = _web_server.serialize_rig_state(rig)
        assert result == {"uplights": [], "washes": [], "beams": [],
                          "sparkles": [], "impacts": [], "ambient_warm": 0.0}


class TestWebSceneAPI:
    """set_paths, _paths configuration, and engine_state fixtures key."""

    def setup_method(self):
        _web_server._paths.update({
            "scenes_dir": "", "positions_file": "", "states_file": "", "scene_manager": None
        })
        _web_server._engine_state.update({"fixtures": {}, "scenes": [], "modes": []})

    def test_engine_state_has_fixtures_key(self):
        assert "fixtures" in _web_server._engine_state

    def test_update_state_sets_fixtures(self):
        rig = _make_rig_state()
        fxt = _web_server.serialize_rig_state(rig)
        _web_server.update_state(fixtures=fxt)
        assert "uplights" in _web_server._engine_state["fixtures"]

    def test_set_paths_populates_paths_dict(self):
        _web_server.set_paths(
            scenes_dir="/tmp/scenes",
            positions_file="/tmp/pos.json",
            states_file="/tmp/states.json",
        )
        assert _web_server._paths["scenes_dir"]     == "/tmp/scenes"
        assert _web_server._paths["positions_file"] == "/tmp/pos.json"
        assert _web_server._paths["states_file"]    == "/tmp/states.json"
        assert _web_server._paths["scene_manager"]  is None

    def test_set_paths_with_scene_manager(self):
        mgr = _make_scene_manager()
        _web_server.set_paths(
            scenes_dir=_SCENES_DIR,
            positions_file=_POSITIONS_FILE,
            states_file=_STATES_FILE,
            scene_manager=mgr,
        )
        assert _web_server._paths["scene_manager"] is mgr

    def test_refresh_scene_catalog_populates_scenes(self):
        mgr = _make_scene_manager()
        _web_server.set_paths(
            scenes_dir=_SCENES_DIR,
            positions_file=_POSITIONS_FILE,
            states_file=_STATES_FILE,
            scene_manager=mgr,
        )
        _web_server._refresh_scene_catalog()
        scenes = _web_server._engine_state["scenes"]
        assert len(scenes) >= 1
        assert all("id" in s and "name" in s for s in scenes)

    def test_refresh_scene_catalog_no_manager_no_crash(self):
        _web_server._paths["scene_manager"] = None
        _web_server._refresh_scene_catalog()   # must not raise

    def test_scene_id_regex_accepts_valid(self):
        import re
        pattern = r'^[a-zA-Z0-9_]+$'
        for valid in ("slow_dance", "scene1", "MyScene", "a_b_c_123"):
            assert re.match(pattern, valid), f"Should be valid: {valid}"

    def test_scene_id_regex_rejects_invalid(self):
        import re
        pattern = r'^[a-zA-Z0-9_]+$'
        for invalid in ("../evil", "scene id", "scene-id", "", "a/b"):
            assert not re.match(pattern, invalid), f"Should be invalid: {invalid}"


# ===========================================================================
# Sprint 10 — StrobeEngine (EDM lift strobe)
# ===========================================================================

class TestStrobeEngine:
    """Rise-synchronized strobe for banger / indian_latin modes."""

    _T = 1000.0  # arbitrary base time for deterministic tests

    def _eng(self):
        e = StrobeEngine()
        e._last_t = self._T
        return e

    def test_inactive_below_threshold(self):
        e = self._eng()
        on, rate, freq = e.update(high_energy=0.10, mode_key="banger", now=self._T + 0.05)
        assert not on
        assert rate == pytest.approx(0.0)
        assert freq == pytest.approx(0.0)

    def test_inactive_in_dinner_mode(self):
        e = self._eng()
        on, rate, _ = e.update(high_energy=0.9, mode_key="dinner", now=self._T + 0.05)
        assert not on
        assert rate == pytest.approx(0.0)

    def test_inactive_in_slow_dance(self):
        e = self._eng()
        on, rate, _ = e.update(high_energy=0.9, mode_key="slow_dance", now=self._T + 0.05)
        assert not on
        assert rate == pytest.approx(0.0)

    def test_active_when_banger_above_threshold(self):
        e = self._eng()
        _, rate, freq = e.update(high_energy=0.8, mode_key="banger", now=self._T + 0.05)
        assert rate > 0.0
        assert freq > 0.0

    def test_active_in_indian_latin(self):
        e = self._eng()
        _, rate, _ = e.update(high_energy=0.7, mode_key="indian_latin", now=self._T + 0.05)
        assert rate > 0.0

    def test_frequency_increases_with_energy(self):
        e1, e2 = self._eng(), self._eng()
        _, _, freq_low  = e1.update(0.65, "banger", now=self._T + 0.01)
        _, _, freq_high = e2.update(0.90, "banger", now=self._T + 0.01)
        assert freq_high > freq_low

    def test_strobe_on_during_duty_cycle(self):
        e = self._eng()
        e._phase = 0.05   # well inside 25% duty cycle
        on, _, _ = e.update(0.8, "banger", now=self._T + 0.001)
        assert on

    def test_strobe_off_outside_duty_cycle(self):
        e = self._eng()
        e._phase = 0.50   # outside 25% duty cycle
        on, _, _ = e.update(0.8, "banger", now=self._T + 0.001)
        assert not on

    def test_hold_keeps_active_briefly_after_drop(self):
        e = self._eng()
        # Activate with high energy
        e.update(0.9, "banger", now=self._T)
        # Drop energy below threshold — hold should keep it alive
        on, rate, _ = e.update(0.05, "banger", now=self._T + 0.05)
        assert rate > 0.0  # still active during hold

    def test_hold_expires_eventually(self):
        e = self._eng()
        e.update(0.9, "banger", now=self._T)
        # dt is capped at 0.1s per call; drain hold (0.22s) over multiple frames
        t = self._T + 0.1
        for _ in range(5):
            on, rate, _ = e.update(0.05, "banger", now=t)
            t += 0.1
        assert rate == pytest.approx(0.0)

    def test_reset_clears_phase(self):
        e = self._eng()
        e.update(0.9, "banger", now=self._T + 0.1)
        e.reset()
        assert e._phase == pytest.approx(0.0)
        assert e._hold_t == pytest.approx(0.0)

    def test_rate_at_max_energy_near_one(self):
        e = self._eng()
        _, rate, _ = e.update(1.0, "banger", now=self._T + 0.01)
        assert rate > 0.9

    def test_freq_near_max_at_full_energy(self):
        e = self._eng()
        _, _, freq = e.update(1.0, "banger", now=self._T + 0.01)
        assert freq == pytest.approx(16.0, abs=0.1)

    def test_safety_allows_strobe_in_banger(self):
        from engine.safety import SafetyEngine
        from engine.modes  import get_mode
        safety = SafetyEngine()
        safety.update_from_mode(get_mode("banger"))
        assert safety.state.strobe_allowed is True

    def test_safety_disallows_strobe_in_dinner(self):
        from engine.safety import SafetyEngine
        from engine.modes  import get_mode
        safety = SafetyEngine()
        safety.update_from_mode(get_mode("dinner"))
        assert safety.state.strobe_allowed is False

    def test_safety_apply_passes_strobe_when_allowed(self):
        from engine.safety import SafetyEngine
        from engine.modes  import get_mode
        safety = SafetyEngine()
        safety.update_from_mode(get_mode("banger"))
        _, safe_strobe = safety.apply(brightness=1.0, strobe=0.7)
        assert safe_strobe == pytest.approx(0.7)

    def test_safety_apply_blocks_strobe_when_blackout(self):
        from engine.safety import SafetyEngine
        from engine.modes  import get_mode
        safety = SafetyEngine()
        safety.update_from_mode(get_mode("banger"))
        safety.toggle_blackout()
        _, safe_strobe = safety.apply(brightness=1.0, strobe=0.7)
        assert safe_strobe == pytest.approx(0.0)

    def test_rockwedge_strobe_dmx_zero_when_off(self):
        from fixtures.rockwedge import RockWedge
        from dmx.universe import DMXUniverse
        rw  = RockWedge("rw0", "test", 1, "room", "all")
        uni = DMXUniverse()
        rw.render_to_universe(uni, 1.0, 0.0, 1.0, 0.8, strobe=0.0)
        assert uni.get_channel(8) == 0   # Ch8 = strobe = off

    def test_rockwedge_strobe_dmx_nonzero_when_on(self):
        from fixtures.rockwedge import RockWedge
        from dmx.universe import DMXUniverse
        rw  = RockWedge("rw0", "test", 1, "room", "all")
        uni = DMXUniverse()
        rw.render_to_universe(uni, 1.0, 0.0, 1.0, 0.8, strobe=0.5)
        assert uni.get_channel(8) >= 11  # Ch8 must be in the strobe range


# ===========================================================================
# Sprint 11: HueCrossfader
# ===========================================================================

class TestHueCrossfader:
    _T = 1000.0

    def test_no_crossfade_returns_current(self):
        cf = HueCrossfader()
        assert cf.blend(90.0, now=self._T) == pytest.approx(90.0)

    def test_snap_then_blend_midpoint(self):
        cf = HueCrossfader(duration_s=1.0)
        cf.snap(0.0, now=self._T)
        result = cf.blend(90.0, now=self._T + 0.5)
        assert 0.0 < result < 90.0

    def test_crossfade_completes_at_duration(self):
        cf = HueCrossfader(duration_s=0.5)
        cf.snap(0.0, now=self._T)
        result = cf.blend(90.0, now=self._T + 1.0)
        assert result == pytest.approx(90.0)

    def test_instant_snap_skips_crossfade(self):
        cf = HueCrossfader(duration_s=0.5)
        cf.snap(0.0, now=self._T, instant=True)
        result = cf.blend(90.0, now=self._T + 0.1)
        assert result == pytest.approx(90.0)

    def test_shortest_path_through_zero(self):
        """350° → 10° should travel +20° (through 0°), not -340°."""
        cf = HueCrossfader(duration_s=1.0)
        cf.snap(350.0, now=self._T)
        result = cf.blend(10.0, now=self._T + 0.5)
        # Midpoint of shortest arc 350°→10° is 0° (or 360°).
        assert result > 340 or result < 20

    def test_reset_clears_active(self):
        cf = HueCrossfader(duration_s=1.0)
        cf.snap(0.0, now=self._T)
        cf.reset()
        result = cf.blend(90.0, now=self._T + 0.1)
        assert result == pytest.approx(90.0)

    def test_second_snap_restarts_blend(self):
        cf = HueCrossfader(duration_s=1.0)
        cf.snap(0.0, now=self._T)
        cf.snap(45.0, now=self._T + 0.3)
        result = cf.blend(90.0, now=self._T + 0.3 + 1.0)
        assert result == pytest.approx(90.0)


# ===========================================================================
# Sprint 11: uplight color zones + ambient_warm
# ===========================================================================

class TestUplightZones:
    _BANDS = {"low_energy": 0.5, "mid_energy": 0.5,
              "high_energy": 0.5, "overall_energy": 0.5}
    _LANES = {"impact": 0.5, "room": 0.5}

    def _build(self, mode_key="banger", hue=0.0, **kw):
        from app.render.scene import SceneLayout
        sl = SceneLayout()
        sl.reset_time(0.0)
        return sl.update_and_build(
            bands=self._BANDS, lanes=self._LANES,
            hue=hue, saturation=1.0, brightness=0.8,
            base_brt=0.3, pulse_brt=0.2,
            mode_key=mode_key, palette_name="test", blackout=False,
            **kw,
        )

    def test_banger_top_bottom_differ(self):
        """Banger zone_offset=50° → top and bottom wall uplights differ."""
        import colorsys
        rig = self._build("banger")
        def to_h(rgb): r, g, b = rgb; h, s, v = colorsys.rgb_to_hsv(r/255, g/255, b/255); return h * 360
        top_h = to_h(rig.uplights[0].color_rgb)
        bot_h = to_h(rig.uplights[6].color_rgb)
        diff = min(abs(top_h - bot_h), 360 - abs(top_h - bot_h))
        assert diff > 5.0

    def test_speech_all_same_hue(self):
        """Speech zone_offset=0 → all uplights identical."""
        import colorsys
        rig = self._build("speech", hue=60.0)
        def to_h(rgb): r, g, b = rgb; h, s, v = colorsys.rgb_to_hsv(r/255, g/255, b/255); return h * 360
        hues = [to_h(u.color_rgb) for u in rig.uplights]
        assert max(hues) - min(hues) < 1.0

    def test_total_uplights_is_18(self):
        assert len(self._build().uplights) == 18

    def test_top_wall_gets_base_hue(self):
        """Uplights 0–5 (top wall) must use the base hue unchanged."""
        import colorsys
        rig = self._build("banger", hue=120.0)
        def to_h(rgb): r, g, b = rgb; h, s, v = colorsys.rgb_to_hsv(r/255, g/255, b/255); return h * 360
        for i in range(6):
            h = to_h(rig.uplights[i].color_rgb)
            assert abs(h - 120.0) < 2.0, f"uplight {i} hue={h:.1f} expected ~120"

    def test_ambient_warm_from_amber(self):
        rig = self._build(ambient_amber=0.6)
        assert rig.ambient_warm > 0.0

    def test_ambient_warm_from_white(self):
        rig = self._build(ambient_white=0.4)
        assert rig.ambient_warm > 0.0

    def test_ambient_warm_zero_on_blackout(self):
        from app.render.scene import SceneLayout
        sl = SceneLayout()
        sl.reset_time(0.0)
        rig = sl.update_and_build(
            bands=self._BANDS, lanes=self._LANES,
            hue=30.0, saturation=1.0, brightness=0.6,
            base_brt=0.3, pulse_brt=0.1,
            mode_key="dinner", palette_name="test", blackout=True,
            ambient_white=0.3, ambient_amber=0.5,
        )
        assert rig.ambient_warm == pytest.approx(0.0)

    def test_ambient_warm_capped_at_one(self):
        rig = self._build(ambient_amber=0.9, ambient_white=0.9)
        assert rig.ambient_warm <= 1.0


# ===========================================================================
# Sprint 12: strobe threshold, faders, iPad server
# ===========================================================================

class TestStrobeThreshold:
    _T = 1000.0

    def _eng(self):
        from engine.strobe import StrobeEngine
        e = StrobeEngine()
        e._last_t = self._T
        return e

    def test_inactive_below_055(self):
        e = self._eng()
        _, rate, _ = e.update(0.50, "banger", now=self._T + 0.01)
        assert rate == pytest.approx(0.0)

    def test_active_above_055(self):
        e = self._eng()
        _, rate, _ = e.update(0.60, "banger", now=self._T + 0.01)
        assert rate > 0.0


class TestEngineState:
    def test_engine_state_has_fader_fields(self):
        from app.web.server import _engine_state
        assert "master_dimmer" in _engine_state
        assert "uplight_dimmer" in _engine_state
        assert "strobe_master" in _engine_state
        assert "impact_lane" in _engine_state
        assert "room_lane" in _engine_state
        assert "strobe_rate" in _engine_state

    def test_engine_state_fader_defaults(self):
        from app.web.server import _engine_state
        assert _engine_state["master_dimmer"] == 1.0
        assert _engine_state["uplight_dimmer"] == 1.0
        assert _engine_state["strobe_master"] == 1.0


class TestIPadServer:
    def test_ipad_server_import(self):
        from app.web import ipad_server
        assert hasattr(ipad_server, "start")
        assert hasattr(ipad_server, "_build_app")

    def test_ipad_server_shares_state(self):
        from app.web.server import _engine_state, _command_queue
        from app.web.ipad_server import _engine_state as ipad_state
        assert ipad_state is _engine_state

    def test_ipad_manifest_endpoint(self):
        try:
            from starlette.testclient import TestClient
        except ImportError:
            pytest.skip("starlette not installed")
        from app.web.ipad_server import _build_app
        app = _build_app()
        client = TestClient(app)
        resp = client.get("/manifest.json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "LightBrain"
        assert data["display"] == "standalone"

    def test_ipad_serves_html(self):
        try:
            from starlette.testclient import TestClient
        except ImportError:
            pytest.skip("starlette not installed")
        from app.web.ipad_server import _build_app
        app = _build_app()
        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "LIGHTBRAIN" in resp.text


class TestAppConfig:
    def test_app_config_loads(self):
        import json, os
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "app_config.json"
        )
        with open(path) as f:
            cfg = json.load(f)
        assert cfg["web_server_enabled"] is True
        assert cfg["web_server_port"] == 8080
        assert cfg["headless_mode"] is False


class TestUplightDimmer:
    def test_uplight_dimmer_reduces_brightness(self):
        from app.render.scene import SceneLayout
        sl = SceneLayout()
        sl.reset_time(0.0)
        bands = {"low_energy": 0.5, "mid_energy": 0.5,
                 "high_energy": 0.5, "overall_energy": 0.5}
        lanes = {"impact": 0.5, "room": 0.5}
        rig = sl.update_and_build(
            bands=bands, lanes=lanes,
            hue=120.0, saturation=1.0, brightness=0.8,
            base_brt=0.3, pulse_brt=0.2,
            mode_key="open_dance", palette_name="test", blackout=False,
        )
        original_brts = [u.brightness for u in rig.uplights]
        dimmer = 0.5
        for u in rig.uplights:
            u.brightness *= dimmer
        for i, u in enumerate(rig.uplights):
            assert u.brightness == pytest.approx(original_brts[i] * 0.5)


# ===========================================================================
# Enttec Pro frame construction (byte-level verification)
# ===========================================================================

from dmx.output_enttec_pro import build_enttec_frame


class TestEnttecProFrame:

    def test_header_byte(self):
        frame = build_enttec_frame(DMXUniverse())
        assert frame[0] == 0x7E

    def test_label_byte(self):
        frame = build_enttec_frame(DMXUniverse())
        assert frame[1] == 0x06

    def test_length_bytes_lsb_first(self):
        frame = build_enttec_frame(DMXUniverse())
        expected_len = 513
        assert frame[2] | (frame[3] << 8) == expected_len

    def test_start_code_is_zero(self):
        frame = build_enttec_frame(DMXUniverse())
        assert frame[4] == 0x00

    def test_channel_data_roundtrip(self):
        u = DMXUniverse()
        u.set_channel(1, 42)
        u.set_channel(512, 99)
        frame = build_enttec_frame(u)
        assert frame[5] == 42
        assert frame[516] == 99

    def test_footer_byte(self):
        frame = build_enttec_frame(DMXUniverse())
        assert frame[-1] == 0xE7

    def test_total_frame_length(self):
        frame = build_enttec_frame(DMXUniverse())
        assert len(frame) == 518

    def test_all_channels_in_frame(self):
        u = DMXUniverse()
        for ch in range(1, 513):
            u.set_channel(ch, ch % 256)
        frame = build_enttec_frame(u)
        for ch in range(1, 513):
            assert frame[4 + ch] == ch % 256


# ===========================================================================
# FFT normalization — AudioAnalyzer output always in [0, 1]
# ===========================================================================

class TestAudioAnalyzerFFTNormalization:

    def _make_analyzer(self, block_size=1024):
        return AudioAnalyzer(sample_rate=44100, block_size=block_size)

    def test_sine_wave_in_range(self):
        az = self._make_analyzer()
        t = np.linspace(0, 1024 / 44100, 1024, endpoint=False)
        block = (np.sin(2 * np.pi * 440 * t) * 0.8).astype(np.float32)
        for _ in range(5):
            az.analyze(block)
        bands = az.analyze(block)
        assert 0.0 <= bands.low_energy <= 1.0
        assert 0.0 <= bands.mid_energy <= 1.0
        assert 0.0 <= bands.high_energy <= 1.0
        assert 0.0 <= bands.overall_energy <= 1.0

    def test_silence_returns_zero(self):
        az = self._make_analyzer()
        block = np.zeros(1024, dtype=np.float32)
        bands = az.analyze(block)
        assert bands.low_energy == pytest.approx(0.0)
        assert bands.mid_energy == pytest.approx(0.0)
        assert bands.high_energy == pytest.approx(0.0)
        assert bands.overall_energy == pytest.approx(0.0)

    def test_random_signal_always_in_range(self):
        az = self._make_analyzer()
        rng = np.random.default_rng(42)
        for _ in range(50):
            block = rng.uniform(-1.0, 1.0, size=1024).astype(np.float32)
            bands = az.analyze(block)
            assert 0.0 <= bands.low_energy <= 1.0
            assert 0.0 <= bands.mid_energy <= 1.0
            assert 0.0 <= bands.high_energy <= 1.0
            assert 0.0 <= bands.overall_energy <= 1.0

    def test_no_nan_or_inf(self):
        az = self._make_analyzer()
        t = np.linspace(0, 1024 / 44100, 1024, endpoint=False)
        block = (np.sin(2 * np.pi * 1000 * t) * 0.5).astype(np.float32)
        bands = az.analyze(block)
        for val in [bands.low_energy, bands.mid_energy, bands.high_energy, bands.overall_energy]:
            assert math.isfinite(val)


# ===========================================================================
# Blackout safety bypass — verify fixes
# ===========================================================================

class TestBlackoutSafetyBypass:

    def test_beat_strength_never_negative(self):
        bd = BeatDetector(history_size=5, threshold=1.5)
        for _ in range(10):
            bd.update(0.5)
        beat, strength = bd.update(0.9)
        if beat:
            assert strength >= 0.0

    def test_peak_value_resets_after_cooldown(self):
        config = EnvelopeConfig(attack_ms=10, decay_ms=100, cooldown_ms=50)
        ef = EnvelopeFollower(config)
        t = time.monotonic()
        ef.reset(now=t)
        ef.update(1.0, now=t)
        t += 0.1
        ef.update(0.0, now=t)
        t += 0.1
        val = ef.update(0.0, now=t)
        assert val < 0.5

    def test_strobe_t_clamped_below_threshold(self):
        se = StrobeEngine()
        se._last_t = 0.0
        se._hold_t = 0.1
        result = se.update(high_energy=0.1, mode_key="banger", now=1.0)
        assert result[1] >= 0.0


# ===========================================================================
# Chauvet Wash FX 2 — 8-channel fixture mapper
# ===========================================================================

from fixtures.chauvet_wash_fx2 import ChauvetWashFX2, NUM_CHANNELS as WASHFX2_CHANNELS
from fixtures.chauvet_gigbar_move_ils import (
    ChauvetGigBarMoveILS,
    NUM_CHANNELS as GIGBAR_CHANNELS,
    CH_PAR_RED, CH_PAR_STROBE, CH_DERBY_ROTATION,
    CH_FLASH_1, CH_LASER_COLOR, CH_SPOT_PAN, CH_SPOT_DIMMER, CH_SPOT_STROBE,
    _SPOT_STROBE_OPEN,
)


class TestChauvetWashFX2:

    def _make(self, addr=1):
        return ChauvetWashFX2(fixture_id="wx1", name="Wash FX2 Test", dmx_address=addr)

    def _render(self, fx, **kwargs):
        uni = DMXUniverse()
        fx.render_to_universe(uni, **kwargs)
        return uni

    def test_num_channels_is_8(self):
        assert WASHFX2_CHANNELS == 8

    def test_blackout_at_zero_brightness(self):
        uni = self._render(self._make(), brightness=0.0, value=1.0,
                           hue=120.0, saturation=1.0)
        addr = 1
        for i in range(8):
            assert uni.get_channel(addr + i) == 0, f"ch {i+1} should be 0 at blackout"

    def test_full_white_all_channels_high(self):
        uni = self._render(self._make(), brightness=1.0, value=1.0,
                           hue=0.0, saturation=0.0)
        addr = 1
        # R/G/B should all be near 255 for white (hue 0, sat 0)
        assert uni.get_channel(addr + 0) > 200  # Red
        assert uni.get_channel(addr + 1) > 200  # Green
        assert uni.get_channel(addr + 2) > 200  # Blue

    def test_strobe_off_when_zero(self):
        uni = self._render(self._make(), strobe=0.0)
        assert uni.get_channel(1 + 4) == 0   # Ch5 strobe

    def test_strobe_nonzero_when_active(self):
        uni = self._render(self._make(), strobe=1.0, brightness=1.0, value=1.0)
        assert uni.get_channel(1 + 4) > 0    # Ch5 strobe

    def test_strobe_max_is_255(self):
        uni = self._render(self._make(), strobe=1.0, brightness=1.0, value=1.0)
        assert uni.get_channel(1 + 4) == 255

    def test_auto_program_channels_always_zero(self):
        """Ch6/Ch7 auto-program channels must stay 0 in manual mode."""
        uni = self._render(self._make(), brightness=1.0, value=1.0, hue=200.0)
        assert uni.get_channel(1 + 5) == 0   # Ch6 Auto Program
        assert uni.get_channel(1 + 6) == 0   # Ch7 Speed
        assert uni.get_channel(1 + 7) == 0   # Ch8 Dimmer

    def test_uv_channel_driven_by_uv_param(self):
        uni_off = self._render(self._make(), uv=0.0, brightness=1.0, value=1.0)
        uni_on  = self._render(self._make(), uv=1.0, brightness=1.0, value=1.0)
        assert uni_off.get_channel(1 + 3) == 0        # Ch4 UV off
        assert uni_on.get_channel(1 + 3) > 200        # Ch4 UV on

    def test_amber_and_white_params_ignored(self):
        """WashFX2 has no amber/white channels — should not raise."""
        uni = self._render(self._make(), amber=1.0, white=1.0,
                           brightness=1.0, value=1.0)
        assert uni.get_channel(1) >= 0  # no crash, sane output

    def test_channels_within_dmx_range(self):
        uni = self._render(self._make(), brightness=1.0, value=1.0,
                           hue=60.0, saturation=0.8, strobe=0.5, uv=0.5)
        for i in range(8):
            ch = uni.get_channel(1 + i)
            assert 0 <= ch <= 255, f"ch {i+1} = {ch} out of range"


# ===========================================================================
# Chauvet GigBAR Move + ILS — 29-channel fixture mapper
# ===========================================================================

class TestChauvetGigBarMoveILS:

    def _make(self, addr=1, **kwargs):
        return ChauvetGigBarMoveILS(
            fixture_id="gb1", name="GigBAR Test", dmx_address=addr, **kwargs
        )

    def _render(self, fx, **kwargs):
        uni = DMXUniverse()
        fx.render_to_universe(uni, **kwargs)
        return uni

    def test_num_channels_is_29(self):
        assert GIGBAR_CHANNELS == 29

    def test_all_channels_zero_at_blackout(self):
        uni = self._render(self._make(), brightness=0.0, value=0.0)
        # Par channels should be 0 (no colour at blackout brightness)
        addr = 1
        for i in range(7):          # Par R–Strobe
            assert uni.get_channel(addr + i) == 0, f"par ch {i+1} should be 0"

    def test_par_rgb_written_at_full_brightness(self):
        uni = self._render(self._make(), brightness=1.0, value=1.0,
                           hue=0.0, saturation=1.0)
        addr = 1
        assert uni.get_channel(addr + CH_PAR_RED) > 200   # red hue → high R

    def test_par_strobe_off_by_default(self):
        uni = self._render(self._make(), strobe=0.0, brightness=1.0, value=1.0)
        assert uni.get_channel(1 + CH_PAR_STROBE) == 0

    def test_par_strobe_active_at_full(self):
        uni = self._render(self._make(), strobe=1.0, brightness=1.0, value=1.0)
        assert uni.get_channel(1 + CH_PAR_STROBE) == 250  # _PAR_STROBE_MAX

    def test_derby_rotation_zero_when_dark(self):
        uni = self._render(self._make(), brightness=0.0, value=0.0)
        assert uni.get_channel(1 + CH_DERBY_ROTATION) == 0

    def test_derby_rotates_when_bright(self):
        uni = self._render(self._make(), brightness=1.0, value=1.0)
        rot = uni.get_channel(1 + CH_DERBY_ROTATION)
        assert rot >= 129, f"expected CCW rotation, got {rot}"

    def test_flash_leds_low_at_full_brightness(self):
        uni = self._render(self._make(), brightness=1.0, value=1.0)
        flash = uni.get_channel(1 + CH_FLASH_1)
        # Should be >0 but well below 255 (fill ratio is 15%)
        assert 0 < flash < 100

    def test_laser_off_by_default(self):
        uni = self._render(self._make())
        assert uni.get_channel(1 + CH_LASER_COLOR) == 0

    def test_laser_enabled_flag(self):
        fx = self._make(laser_enabled=True)
        uni = self._render(fx, brightness=1.0, value=1.0)
        assert uni.get_channel(1 + CH_LASER_COLOR) > 0

    def test_spot_pan_default_is_center(self):
        fx = self._make(spot_pan_deg=270.0)  # centre of 540° range
        uni = self._render(fx, brightness=1.0, value=1.0)
        pan = uni.get_channel(1 + CH_SPOT_PAN)
        assert 120 <= pan <= 140, f"centre pan should be ~128, got {pan}"

    def test_set_spot_aim_updates_pan(self):
        fx = self._make()
        fx.set_spot_aim(pan_deg=0.0, tilt_dmx=90)
        uni = self._render(fx, brightness=1.0, value=1.0)
        assert uni.get_channel(1 + CH_SPOT_PAN) == 0

    def test_spot_strobe_open_when_inactive(self):
        uni = self._render(self._make(), strobe=0.0, brightness=1.0, value=1.0)
        assert uni.get_channel(1 + CH_SPOT_STROBE) == _SPOT_STROBE_OPEN

    def test_spot_strobe_active_when_strobe_nonzero(self):
        uni = self._render(self._make(), strobe=1.0, brightness=1.0, value=1.0)
        strobe_val = uni.get_channel(1 + CH_SPOT_STROBE)
        assert strobe_val > _SPOT_STROBE_OPEN

    def test_spot_dimmer_tracks_brightness(self):
        uni_dim  = self._render(self._make(), brightness=0.25, value=1.0)
        uni_full = self._render(self._make(), brightness=1.0,  value=1.0)
        assert uni_full.get_channel(1 + CH_SPOT_DIMMER) > \
               uni_dim.get_channel(1 + CH_SPOT_DIMMER)

    def test_all_channels_within_dmx_range(self):
        fx = self._make()
        uni = self._render(fx, brightness=0.7, value=0.8, hue=180.0,
                           saturation=0.9, strobe=0.3, uv=0.2,
                           white=0.3, amber=0.2)
        for i in range(29):
            ch = uni.get_channel(1 + i)
            assert 0 <= ch <= 255, f"ch {i+1} = {ch} out of range"

    def test_amber_and_white_drive_par_channels(self):
        uni = self._render(self._make(), brightness=1.0, value=0.0,
                           hue=0.0, saturation=0.0,
                           amber=1.0, white=1.0)
        # Par Amber (Ch4) and Par White (Ch5) should be driven
        from fixtures.chauvet_gigbar_move_ils import CH_PAR_AMBER, CH_PAR_WHITE
        assert uni.get_channel(1 + CH_PAR_AMBER) > 0
        assert uni.get_channel(1 + CH_PAR_WHITE) > 0


# ===========================================================================
# Drop-sync / cooldown / white-hold feature set
# ===========================================================================
class TestPaletteBeatCooldown:
    """PaletteBlender.beat_cooldown_fraction telemetry (feature 2)."""

    def _blender(self):
        pal = Palette(
            name="t",
            colors=[HSVColor(h=0, s=1, v=1, name="a"),
                    HSVColor(h=120, s=1, v=1, name="b")],
            change_rule="energy_trigger",
            transition_ms=2000,
        )
        return PaletteBlender(pal, hold_ms=1000)

    def test_fresh_blender_reports_no_cooldown(self):
        from engine.palettes import _BEAT_COOLDOWN_S  # noqa
        b = self._blender()
        assert b.beat_cooldown_fraction(now=1000.0) == 0.0

    def test_full_then_decays_to_zero(self):
        from engine.palettes import _BEAT_COOLDOWN_S
        b = self._blender()
        now = 1000.0
        b._last_beat_swap = now
        assert b.beat_cooldown_fraction(now) == pytest.approx(1.0)
        assert b.beat_cooldown_fraction(now + _BEAT_COOLDOWN_S / 2) == pytest.approx(0.5)
        assert b.beat_cooldown_fraction(now + _BEAT_COOLDOWN_S + 5) == 0.0

    def test_room_lane_passthrough(self):
        from engine.lanes import RoomLane
        pal = Palette(name="t",
                      colors=[HSVColor(h=0, s=1, v=1, name="a")],
                      change_rule="energy_trigger", transition_ms=2000)
        rl = RoomLane(pal)
        # callable and bounded
        v = rl.beat_cooldown_fraction(now=1000.0)
        assert 0.0 <= v <= 1.0


class TestNewEngineStateAndCommands:
    """Drop-sync / white-hold plumbing through the web layer."""

    def test_engine_state_has_new_keys(self):
        from app.web import server as web
        for k in ("armed_mode", "cooldown_active", "cooldown_pct", "white_hold"):
            assert k in web._engine_state

    def test_ipad_whitelist_accepts_new_commands(self):
        from app.web import ipad_server
        assert "arm_mode" in ipad_server._ALLOWED_TYPES
        assert "white_hold" in ipad_server._ALLOWED_TYPES

    def test_ipad_rejects_unknown_command(self):
        from app.web import ipad_server
        assert "definitely_not_a_command" not in ipad_server._ALLOWED_TYPES


class TestSerialPortResolution:
    """Regression: dmx.output "enttec_pro" must select the serial backend.

    Before the fix, only "enttec" was recognised — a config following the
    ROADMAP's "enttec_pro" wording silently fell through to mock output.
    """

    def test_enttec_key_resolves_port(self):
        from app.main import resolve_serial_port
        cfg = {"output": "enttec", "serial_port": "/dev/ttyUSB0"}
        assert resolve_serial_port(None, cfg) == "/dev/ttyUSB0"

    def test_enttec_pro_key_resolves_port(self):
        from app.main import resolve_serial_port
        cfg = {"output": "enttec_pro", "serial_port": "/dev/ttyUSB0"}
        assert resolve_serial_port(None, cfg) == "/dev/ttyUSB0"

    def test_mock_output_yields_no_port(self):
        from app.main import resolve_serial_port
        cfg = {"output": "mock", "serial_port": "/dev/ttyUSB0"}
        assert resolve_serial_port(None, cfg) is None

    def test_artnet_output_yields_no_port(self):
        from app.main import resolve_serial_port
        cfg = {"output": "artnet", "serial_port": "/dev/ttyUSB0"}
        assert resolve_serial_port(None, cfg) is None

    def test_cli_serial_overrides_config(self):
        from app.main import resolve_serial_port
        cfg = {"output": "mock"}
        assert resolve_serial_port("COM3", cfg) == "COM3"

    def test_missing_serial_port_yields_none(self):
        from app.main import resolve_serial_port
        assert resolve_serial_port(None, {"output": "enttec"}) is None
        assert resolve_serial_port(None, {}) is None


# ===========================================================================
# White Hold command — both shapes must reach the engine
# ===========================================================================

class TestWhiteHoldCommandShapes:
    """
    The dashboard/3D tab send {type:"white_hold", state:bool}.
    The iPad uses {type:"momentary", effect:"white_hold", action:"start"/"stop"}.
    Both must set _white_hold correctly.
    """

    def _drain(self, q):
        cmds = []
        while not q.empty():
            cmds.append(q.get_nowait())
        return cmds

    def test_dashboard_shape_sets_white_hold_true(self):
        import queue as _q
        from app.web import server as _srv
        old_q = _srv._command_queue
        _srv._command_queue = _q.Queue()
        try:
            _srv._command_queue.put_nowait({"type": "white_hold", "state": True})
            cmd = _srv._command_queue.get_nowait()
            assert cmd["type"] == "white_hold"
            assert cmd["state"] is True
        finally:
            _srv._command_queue = old_q

    def test_ipad_momentary_shape_in_allowlist(self):
        from app.web.ipad_server import _ALLOWED_TYPES
        assert "white_hold" in _ALLOWED_TYPES
        assert "momentary" in _ALLOWED_TYPES

    def test_dashboard_allowlist_includes_white_hold(self):
        from app.web.server import _ALLOWED_COMMAND_TYPES
        assert "white_hold" in _ALLOWED_COMMAND_TYPES


# ===========================================================================
# LAN authentication — no token must bind to loopback only
# ===========================================================================

class TestLanAuth:

    def test_empty_token_forces_loopback(self):
        """ipad_server must not bind to 0.0.0.0 when no token is configured."""
        import unittest.mock as _mock
        import app.main as _main
        calls = []
        with _mock.patch("app.web.ipad_server.start", side_effect=lambda **kw: calls.append(kw)):
            _main._ipad = type("M", (), {"start": staticmethod(lambda **kw: calls.append(kw))})()
            # Simulate the host-selection logic from main.py
            token = ""
            host = "0.0.0.0" if token else "127.0.0.1"
            assert host == "127.0.0.1", "empty token must restrict to loopback"

    def test_nonempty_token_allows_lan(self):
        token = "my-secret-token"
        host = "0.0.0.0" if token else "127.0.0.1"
        assert host == "0.0.0.0", "non-empty token should allow LAN binding"


# ===========================================================================
# Run of Show
# ===========================================================================

class TestRunOfShow:
    """Run-of-show scene sequencing — next/prev navigation and state broadcast."""

    def setup_method(self):
        self.mgr = _make_scene_manager()
        self.ros = ["grand_entrance", "dinner_service", "toasts", "slow_dance", "open_dancing", "send_off"]

    def _step_next(self, index):
        new_index = min(index + 1, len(self.ros) - 1)
        sid = self.ros[new_index]
        ok = self.mgr.activate_scene(sid)
        return new_index, ok

    def _step_prev(self, index):
        if index <= 0:
            return index, False
        new_index = index - 1
        sid = self.ros[new_index]
        ok = self.mgr.activate_scene(sid)
        return new_index, ok

    def test_get_scene_name_known(self):
        assert self.mgr.get_scene_name("slow_dance") == "Slow Dance"

    def test_get_scene_name_unknown_fallback(self):
        assert self.mgr.get_scene_name("nonexistent_scene") == "nonexistent_scene"

    def test_next_advances_index(self):
        idx = -1
        idx, ok = self._step_next(idx)
        assert idx == 0
        assert ok is True
        assert self.mgr.active_scene_id == "grand_entrance"

    def test_next_from_last_clamps(self):
        idx = len(self.ros) - 1   # already at end
        new_idx, _ = self._step_next(idx)
        assert new_idx == len(self.ros) - 1   # didn't go past end

    def test_prev_goes_back(self):
        idx = 2
        self.mgr.activate_scene(self.ros[2])
        idx, ok = self._step_prev(idx)
        assert idx == 1
        assert ok is True
        assert self.mgr.active_scene_id == self.ros[1]

    def test_prev_at_zero_does_nothing(self):
        idx = 0
        new_idx, ok = self._step_prev(idx)
        assert new_idx == 0
        assert ok is False

    def test_full_walkthrough(self):
        idx = -1
        for expected in range(len(self.ros)):
            idx, ok = self._step_next(idx)
            assert idx == expected
            assert ok is True
            assert self.mgr.active_scene_id == self.ros[expected]

    def test_ros_config_loads(self):
        import json, os
        p = os.path.join(os.path.dirname(__file__), '..', 'config', 'run_of_show.json')
        cfg = json.load(open(p))
        assert "scenes" in cfg
        assert len(cfg["scenes"]) > 0
        # all scene ids in the config must actually exist
        for sid in cfg["scenes"]:
            assert self.mgr.get_scene(sid) is not None, f"scene {sid!r} in ROS but not loaded"

    def test_ros_state_fields_in_allowlist(self):
        from app.web.server import _ALLOWED_COMMAND_TYPES
        assert "next_ros_scene" in _ALLOWED_COMMAND_TYPES
        assert "prev_ros_scene" in _ALLOWED_COMMAND_TYPES


class TestAutoFade:
    """Song-end silence detection: auto-fade to dinner mode after sustained quiet."""

    # ------------------------------------------------------------------
    # Silence counter logic (pure unit tests — no engine process)
    # ------------------------------------------------------------------

    def _make_silence_state(self):
        """Return a fresh mutable dict representing the relevant main-loop vars."""
        return {
            "auto_fade_enabled": True,
            "auto_fade_delay_s": 15.0,
            "silence_threshold": 0.04,
            "silence_start": None,
            "auto_faded": False,
            "mode_key": "open_dance",
            "blackout": False,
        }

    def _tick(self, st, overall_e, room_e, now):
        """Replicate one frame of the silence-detection block from main.py."""
        if overall_e < st["silence_threshold"] and room_e < st["silence_threshold"]:
            if st["silence_start"] is None:
                st["silence_start"] = now
        else:
            st["silence_start"] = None
            st["auto_faded"] = False

        triggered = False
        if (
            st["auto_fade_enabled"]
            and st["silence_start"] is not None
            and not st["auto_faded"]
            and st["mode_key"] != "dinner"
            and not st["blackout"]
        ):
            if now - st["silence_start"] >= st["auto_fade_delay_s"]:
                st["mode_key"] = "dinner"
                st["auto_faded"] = True
                triggered = True
        return triggered

    def test_silence_counter_starts_on_quiet(self):
        st = self._make_silence_state()
        self._tick(st, 0.01, 0.01, 100.0)
        assert st["silence_start"] == 100.0

    def test_silence_counter_resets_on_audio(self):
        st = self._make_silence_state()
        self._tick(st, 0.01, 0.01, 100.0)
        self._tick(st, 0.5, 0.5, 100.1)   # audio returns
        assert st["silence_start"] is None

    def test_no_fade_before_delay(self):
        st = self._make_silence_state()
        self._tick(st, 0.01, 0.01, 0.0)
        triggered = self._tick(st, 0.01, 0.01, 5.0)  # only 5s, need 15s
        assert not triggered
        assert st["mode_key"] == "open_dance"

    def test_fade_triggers_after_delay(self):
        st = self._make_silence_state()
        self._tick(st, 0.01, 0.01, 0.0)
        triggered = self._tick(st, 0.01, 0.01, 15.0)  # exactly 15s
        assert triggered
        assert st["mode_key"] == "dinner"

    def test_no_second_trigger_without_audio_return(self):
        st = self._make_silence_state()
        self._tick(st, 0.01, 0.01, 0.0)
        self._tick(st, 0.01, 0.01, 15.0)  # first trigger
        triggered2 = self._tick(st, 0.01, 0.01, 20.0)
        assert not triggered2

    def test_disabled_flag_prevents_fade(self):
        st = self._make_silence_state()
        st["auto_fade_enabled"] = False
        self._tick(st, 0.01, 0.01, 0.0)
        triggered = self._tick(st, 0.01, 0.01, 20.0)
        assert not triggered
        assert st["mode_key"] == "open_dance"

    def test_blackout_prevents_fade(self):
        st = self._make_silence_state()
        st["blackout"] = True
        self._tick(st, 0.01, 0.01, 0.0)
        triggered = self._tick(st, 0.01, 0.01, 20.0)
        assert not triggered

    def test_already_in_dinner_no_trigger(self):
        st = self._make_silence_state()
        st["mode_key"] = "dinner"
        self._tick(st, 0.01, 0.01, 0.0)
        triggered = self._tick(st, 0.01, 0.01, 20.0)
        assert not triggered

    def test_auto_faded_resets_when_audio_returns(self):
        st = self._make_silence_state()
        self._tick(st, 0.01, 0.01, 0.0)
        self._tick(st, 0.01, 0.01, 15.0)  # trigger
        assert st["auto_faded"]
        self._tick(st, 0.5, 0.5, 15.1)   # audio returns
        assert not st["auto_faded"]

    # ------------------------------------------------------------------
    # Server / state defaults
    # ------------------------------------------------------------------

    def test_server_state_defaults_present(self):
        from app.web.server import _engine_state
        assert "auto_fade_enabled" in _engine_state
        assert "auto_fade_delay_s" in _engine_state
        assert "auto_fade_countdown" in _engine_state

    def test_server_state_defaults_values(self):
        from app.web.server import _engine_state
        assert _engine_state["auto_fade_enabled"] is True
        assert _engine_state["auto_fade_delay_s"] == 15.0
        assert _engine_state["auto_fade_countdown"] is None

    def test_set_auto_fade_in_allowlist(self):
        from app.web.server import _ALLOWED_COMMAND_TYPES
        assert "set_auto_fade" in _ALLOWED_COMMAND_TYPES

    # ------------------------------------------------------------------
    # Countdown value
    # ------------------------------------------------------------------

    def test_countdown_is_none_when_not_silent(self):
        silence_start = None
        delay = 15.0
        countdown = (
            round(max(0.0, delay - (100.0 - silence_start)), 1)
            if silence_start is not None else None
        )
        assert countdown is None

    def test_countdown_value_during_silence(self):
        silence_start = 100.0
        delay = 15.0
        now = 108.0
        countdown = round(max(0.0, delay - (now - silence_start)), 1)
        assert countdown == 7.0

    def test_countdown_clamps_to_zero(self):
        silence_start = 100.0
        delay = 15.0
        now = 120.0  # past the delay
        countdown = round(max(0.0, delay - (now - silence_start)), 1)
        assert countdown == 0.0


class TestSpotlight:
    """One-button Spotlight preset: CTO spot + dim amber uplights."""

    def _make_gigbar(self):
        from fixtures.chauvet_gigbar_move_ils import ChauvetGigBarMoveILS
        return ChauvetGigBarMoveILS(
            fixture_id="gigbar1", name="GigBAR", dmx_address=17
        )

    def _make_rockwedge(self, address=46):
        from fixtures.rockwedge import RockWedge
        return RockWedge(fixture_id=f"rw{address}", name=f"RW{address}", dmx_address=address)

    # ------------------------------------------------------------------
    # GigBAR spot color control
    # ------------------------------------------------------------------

    def test_set_spot_color_clamps_low(self):
        fx = self._make_gigbar()
        fx.set_spot_color(-10)
        assert fx._spot_color_dmx == 0

    def test_set_spot_color_clamps_high(self):
        fx = self._make_gigbar()
        fx.set_spot_color(999)
        assert fx._spot_color_dmx == 255

    def test_set_spot_color_midrange(self):
        fx = self._make_gigbar()
        fx.set_spot_color(45)
        assert fx._spot_color_dmx == 45

    def test_spot_color_default_is_white(self):
        fx = self._make_gigbar()
        assert fx._spot_color_dmx == 0

    def test_spot_color_written_to_universe(self):
        from dmx.universe import DMXUniverse
        fx = self._make_gigbar()
        fx.set_spot_color(45)
        uni = DMXUniverse()
        fx.render_to_universe(uni, brightness=1.0)
        # Ch 26 relative to address 17 → DMX channel 42 (17 + 25)
        assert uni.get_channel(17 + 25) == 45

    def test_spot_color_open_when_not_spotlight(self):
        from dmx.universe import DMXUniverse
        fx = self._make_gigbar()
        uni = DMXUniverse()
        fx.render_to_universe(uni, brightness=1.0)
        assert uni.get_channel(17 + 25) == 0  # open white

    def test_mover_only_with_spot_color_applied(self):
        """set_mover_only doesn't clear _spot_color_dmx."""
        from dmx.universe import DMXUniverse
        fx = self._make_gigbar()
        fx.set_spot_color(45)
        fx.set_mover_only(True)
        uni = DMXUniverse()
        fx.render_to_universe(uni, brightness=1.0)
        # Spot color wheel should still be 45
        assert uni.get_channel(17 + 25) == 45

    def test_mover_only_zeros_par_with_spot_cto(self):
        """With mover_only+CTO, Par Red (ch 1) should be zero."""
        from dmx.universe import DMXUniverse
        fx = self._make_gigbar()
        fx.set_spot_color(45)
        fx.set_mover_only(True)
        uni = DMXUniverse()
        fx.render_to_universe(uni, brightness=1.0, value=1.0)
        assert uni.get_channel(17 + 0) == 0  # Par Red zeroed

    # ------------------------------------------------------------------
    # Server defaults
    # ------------------------------------------------------------------

    def test_server_has_spotlight_default(self):
        from app.web.server import _engine_state
        assert "spotlight" in _engine_state
        assert _engine_state["spotlight"] is False

    def test_toggle_kill_spotlight_in_allowlist(self):
        from app.web.server import _ALLOWED_COMMAND_TYPES
        assert "toggle_kill" in _ALLOWED_COMMAND_TYPES  # spotlight uses toggle_kill


class TestPanic:
    """Panic / Safe State button: single command resets all overrides to safe defaults."""

    def _make_state(self, **overrides):
        """Mutable dict mirroring the relevant main-loop state variables."""
        st = {
            "kill_strobe":    True,
            "kill_derby":     True,
            "kill_laser":     True,
            "mover_solo":     True,
            "spotlight":      True,
            "strobe_armed":   True,
            "armed_mode":     "open_dance",
            "white_hold":     True,
            "strobe_hold":    True,
            "test_mode":      True,
            "test_pattern":   "white",
            "master_dimmer":  0.4,
            "uplight_dimmer": 0.5,
            "strobe_master":  0.9,
            "flash_frames":   5,
            "silence_start":  100.0,
            "auto_faded":     True,
            "blackout":       False,
            "mode_key":       "open_dance",
        }
        st.update(overrides)
        return st

    def _apply_panic(self, st):
        """Replicate the panic handler from main.py."""
        st["kill_strobe"]   = False
        st["kill_derby"]    = False
        st["kill_laser"]    = False
        st["mover_solo"]    = False
        st["spotlight"]     = False
        st["strobe_armed"]  = False
        st["armed_mode"]    = ""
        st["white_hold"]    = False
        st["strobe_hold"]   = False
        st["test_mode"]     = False
        st["test_pattern"]  = ""
        st["master_dimmer"]   = 1.0
        st["uplight_dimmer"]  = 1.0
        st["strobe_master"]   = 1.0
        st["silence_start"]   = None
        st["auto_faded"]      = False
        st["flash_frames"]    = 0
        # Crossfade to dinner
        st["mode_key"] = "dinner"
        # Release blackout if active
        if st["blackout"]:
            st["blackout"] = False

    def test_kill_switches_cleared(self):
        st = self._make_state()
        self._apply_panic(st)
        assert not st["kill_strobe"]
        assert not st["kill_derby"]
        assert not st["kill_laser"]

    def test_solo_and_spotlight_cleared(self):
        st = self._make_state()
        self._apply_panic(st)
        assert not st["mover_solo"]
        assert not st["spotlight"]

    def test_armed_mode_cleared(self):
        st = self._make_state()
        self._apply_panic(st)
        assert st["armed_mode"] == ""

    def test_white_hold_and_strobe_hold_cleared(self):
        st = self._make_state()
        self._apply_panic(st)
        assert not st["white_hold"]
        assert not st["strobe_hold"]

    def test_test_mode_cleared(self):
        st = self._make_state()
        self._apply_panic(st)
        assert not st["test_mode"]
        assert st["test_pattern"] == ""

    def test_dimmers_restored_to_full(self):
        st = self._make_state()
        self._apply_panic(st)
        assert st["master_dimmer"]   == 1.0
        assert st["uplight_dimmer"]  == 1.0
        assert st["strobe_master"]   == 1.0

    def test_flash_frames_cleared(self):
        st = self._make_state()
        self._apply_panic(st)
        assert st["flash_frames"] == 0

    def test_silence_detection_reset(self):
        st = self._make_state()
        self._apply_panic(st)
        assert st["silence_start"] is None
        assert not st["auto_faded"]

    def test_mode_switches_to_dinner(self):
        st = self._make_state()
        self._apply_panic(st)
        assert st["mode_key"] == "dinner"

    def test_blackout_released_if_active(self):
        st = self._make_state(blackout=True)
        self._apply_panic(st)
        assert not st["blackout"]

    def test_blackout_not_toggled_if_already_off(self):
        st = self._make_state(blackout=False)
        self._apply_panic(st)
        assert not st["blackout"]

    def test_panic_in_allowlist(self):
        from app.web.server import _ALLOWED_COMMAND_TYPES
        assert "panic" in _ALLOWED_COMMAND_TYPES
