"""
LightBrain Sprint 1 automated test suite.

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
from engine.modes     import get_mode
from engine.safety    import SafetyEngine
from engine.lanes     import RoomLane
from audio.synthetic  import SyntheticAudioSource
from audio.analyzer   import AudioAnalyzer


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
