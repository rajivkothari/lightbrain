"""
EMA smoothing physics tests.

Validates the asymmetric envelope follower against real-world fixture
constraints:
  1. Asymmetric alpha correctness (attack faster than decay)
  2. Decay floor clamping (no subnormal drift, no DMX 1-2 lingering)
  3. Transient dominance (new peak instantly overrides active decay)
  4. Mode profile switching (reconfigure without state reset)
  5. Alpha coefficient accuracy against closed-form formula
"""

import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine.smoothing import (
    EnvelopeConfig,
    EnvelopeFollower,
    LaneSmoother,
    ModeEMAProfile,
    MODE_EMA_PROFILES,
    _DECAY_FLOOR,
    get_mode_ema_profile,
)


DT_S = 0.025  # 40 Hz frame period


def _make_follower(attack_ms: float = 50, decay_ms: float = 200,
                   cooldown_ms: float = 0, min_threshold: float = 0.0,
                   initial: float = 0.0) -> EnvelopeFollower:
    cfg = EnvelopeConfig(attack_ms=attack_ms, decay_ms=decay_ms,
                         cooldown_ms=cooldown_ms, min_threshold=min_threshold)
    f = EnvelopeFollower(cfg, initial_value=initial)
    f.reset(value=initial, now=0.0)
    return f


def _run_frames(f: EnvelopeFollower, raw: float, n_frames: int,
                start_t: float = 0.0) -> list[float]:
    """Feed `n_frames` of constant `raw` input and return all outputs."""
    results = []
    t = start_t
    for _ in range(n_frames):
        t += DT_S
        results.append(f.update(raw, now=t))
    return results


# ---------------------------------------------------------------------------
# 1. Asymmetric alpha correctness
# ---------------------------------------------------------------------------

class TestAsymmetricAlpha:
    """Attack must be faster than decay given attack_ms < decay_ms."""

    def test_attack_reaches_half_faster_than_decay(self):
        # Attack from 0→1
        f = _make_follower(attack_ms=50, decay_ms=500)
        attack_out = _run_frames(f, 1.0, 200)
        half_up = next((i for i, v in enumerate(attack_out) if v >= 0.5), None)

        # Decay from 1→0: first ramp up to 1.0, then decay
        f2 = _make_follower(attack_ms=1, decay_ms=500)
        _run_frames(f2, 1.0, 40)  # ramp to ~1.0 with fast attack
        decay_out = _run_frames(f2, 0.0, 200, start_t=40 * DT_S)
        half_down = next((i for i, v in enumerate(decay_out) if v <= 0.5), None)

        assert half_up is not None and half_down is not None
        assert half_up < half_down, (
            f"Attack reached 0.5 at frame {half_up}, decay at {half_down} — "
            f"attack should be faster"
        )

    def test_attack_alpha_matches_formula(self):
        """alpha_attack = 1 - exp(-dt / tau_attack) at dt=25ms, tau=50ms."""
        tau_ms  = 50.0
        alpha   = 1.0 - math.exp(-DT_S / (tau_ms / 1000.0))
        f       = _make_follower(attack_ms=tau_ms, decay_ms=1000)
        out     = f.update(1.0, now=DT_S)
        # First frame from 0.0: out = alpha * (1.0 - 0.0) = alpha
        assert abs(out - alpha) < 1e-9, f"Expected {alpha:.9f}, got {out:.9f}"

    def test_decay_alpha_matches_formula(self):
        """alpha_decay = 1 - exp(-dt / tau_decay) at dt=25ms, tau=200ms."""
        tau_ms  = 200.0
        alpha   = 1.0 - math.exp(-DT_S / (tau_ms / 1000.0))
        f       = _make_follower(attack_ms=5, decay_ms=tau_ms, initial=1.0)
        out     = f.update(0.0, now=DT_S)
        expected = 1.0 + alpha * (0.0 - 1.0)  # = 1.0 - alpha
        assert abs(out - expected) < 1e-9, f"Expected {expected:.9f}, got {out:.9f}"

    def test_zero_tau_gives_instant_tracking(self):
        f = _make_follower(attack_ms=0, decay_ms=0)
        assert f.update(0.75, now=DT_S) == 0.75
        assert f.update(0.25, now=2 * DT_S) == 0.25

    def test_very_long_decay_barely_moves(self):
        """With tau_decay=10s, one frame should barely change the value."""
        f = _make_follower(attack_ms=5, decay_ms=10000, initial=1.0)
        out = f.update(0.0, now=DT_S)
        assert out > 0.99, f"Expected >0.99 after one 25ms frame with 10s decay, got {out}"


# ---------------------------------------------------------------------------
# 2. Decay floor clamping
# ---------------------------------------------------------------------------

class TestDecayFloor:
    """Smoothed value must snap to exactly 0.0 once it decays below _DECAY_FLOOR."""

    def test_decay_reaches_exact_zero(self):
        f = _make_follower(attack_ms=5, decay_ms=50, initial=1.0)
        # Run 200 frames (~5s) of silence — long enough for any tau
        outputs = _run_frames(f, 0.0, 200)
        assert outputs[-1] == 0.0, (
            f"After 200 frames of silence, value should be exactly 0.0, "
            f"got {outputs[-1]}"
        )

    def test_floor_triggers_below_threshold(self):
        """Value should snap to 0.0 the moment it drops below _DECAY_FLOOR."""
        f = _make_follower(attack_ms=5, decay_ms=100, initial=_DECAY_FLOOR * 2)
        # Feed silence until it drops below floor
        outputs = _run_frames(f, 0.0, 50)
        # Find the first zero
        first_zero = next((i for i, v in enumerate(outputs) if v == 0.0), None)
        assert first_zero is not None, "Value never reached exact 0.0"
        # All subsequent frames must also be 0.0
        for v in outputs[first_zero:]:
            assert v == 0.0

    def test_floor_only_active_when_raw_is_zero(self):
        """If raw input is nonzero but small, floor clamping must not trigger."""
        f = _make_follower(attack_ms=5, decay_ms=50, initial=0.0)
        # Feed a very small but nonzero signal
        tiny_signal = _DECAY_FLOOR * 0.5
        out = f.update(tiny_signal, now=DT_S)
        # The value should be tracking toward tiny_signal, not forced to 0
        # (min_threshold=0 so the raw passes through)
        assert out > 0.0 or tiny_signal == 0.0

    def test_decay_floor_constant_value(self):
        assert _DECAY_FLOOR > 0.0
        assert _DECAY_FLOOR < 0.01

    def test_no_subnormal_lingering(self):
        """After a pulse, value must reach 0.0 — never lingers at a subnormal."""
        f = _make_follower(attack_ms=10, decay_ms=300, initial=0.0)
        # Hit a pulse
        f.update(1.0, now=DT_S)
        f.update(1.0, now=2 * DT_S)
        # Decay for 400 frames (~10s)
        outputs = _run_frames(f, 0.0, 400, start_t=2 * DT_S)
        assert outputs[-1] == 0.0


# ---------------------------------------------------------------------------
# 3. Transient dominance over decay
# ---------------------------------------------------------------------------

class TestTransientDominance:
    """A new peak during active decay must instantly take over via attack alpha."""

    def test_new_peak_overrides_decay(self):
        f = _make_follower(attack_ms=10, decay_ms=500)
        # Hit a peak
        _run_frames(f, 1.0, 4)
        peak_val = f.value

        # Decay for 10 frames
        decay_vals = _run_frames(f, 0.0, 10, start_t=4 * DT_S)
        mid_decay = f.value
        assert mid_decay < peak_val, "Should be decaying"

        # Hit a second peak during decay
        new_start_t = (4 + 10) * DT_S
        f.update(1.0, now=new_start_t + DT_S)
        assert f.value > mid_decay, (
            f"New peak should instantly raise value from {mid_decay} "
            f"but got {f.value}"
        )

    def test_no_discontinuity_on_attack_during_decay(self):
        """The transition from decay to attack must be smooth (no jump/wrap)."""
        f = _make_follower(attack_ms=25, decay_ms=200)
        _run_frames(f, 0.8, 8)
        # Decay 5 frames
        vals_before = _run_frames(f, 0.0, 5, start_t=8 * DT_S)
        val_before_hit = f.value
        # New hit at 0.6 (below the original peak but above current decayed value)
        val_after_hit = f.update(0.6, now=(8 + 5) * DT_S + DT_S)

        # Value must have increased (attack kicked in)
        assert val_after_hit > val_before_hit
        # But must not exceed the raw input target (no overshoot)
        assert val_after_hit <= 0.6 + 1e-9

    def test_rapid_double_tap(self):
        """Two hits in quick succession: second hit starts from first's level."""
        f = _make_follower(attack_ms=10, decay_ms=100)
        v1 = f.update(0.5, now=DT_S)
        v2 = f.update(0.9, now=2 * DT_S)
        assert v2 > v1, "Second hit should be higher than first"
        assert v2 < 0.9, "Should not reach target in one frame with tau=10ms"


# ---------------------------------------------------------------------------
# 4. Mode profile switching
# ---------------------------------------------------------------------------

class TestModeProfileSwitch:
    """apply_mode_profile must swap configs without resetting smoothed state."""

    def test_reconfigure_preserves_value(self):
        cfg1 = EnvelopeConfig(attack_ms=50, decay_ms=200, cooldown_ms=0)
        cfg2 = EnvelopeConfig(attack_ms=5,  decay_ms=100, cooldown_ms=0)
        f = EnvelopeFollower(cfg1)
        f.reset(now=0.0)
        _run_frames(f, 0.8, 10)
        val_before = f.value
        f.reconfigure(cfg2)
        assert f.value == val_before, "reconfigure must not change smoothed value"

    def test_lane_smoother_apply_preserves_state(self):
        s = LaneSmoother("dinner")
        s.reset_all(now=0.0)
        bands = {"low_energy": 0.5, "mid_energy": 0.3,
                 "high_energy": 0.2, "overall_energy": 0.4}
        s.update(bands, now=0.025)
        s.update(bands, now=0.050)
        impact_before = s.impact.value
        room_before   = s.room.value
        s.apply_mode_profile("banger")
        assert s.impact.value == impact_before
        assert s.room.value == room_before

    def test_mode_switch_changes_attack_speed(self):
        """Switching dinner→banger should make the next impact frame snap faster."""
        s = LaneSmoother("dinner")
        s.reset_all(now=0.0)
        # One frame of input in dinner mode
        bands = {"low_energy": 0.8, "mid_energy": 0.3,
                 "high_energy": 0.2, "overall_energy": 0.5}
        dinner_impact = s.impact.update(0.8, now=DT_S)

        # Switch to banger, same input, same dt
        s2 = LaneSmoother("banger")
        s2.reset_all(now=0.0)
        banger_impact = s2.impact.update(0.8, now=DT_S)

        assert banger_impact > dinner_impact, (
            f"Banger impact attack ({banger_impact:.4f}) should be faster than "
            f"dinner ({dinner_impact:.4f})"
        )

    def test_all_modes_have_profiles(self):
        expected_modes = {"dinner", "speech", "slow_dance", "open_dance",
                          "banger", "indian_latin"}
        assert expected_modes <= set(MODE_EMA_PROFILES.keys())

    def test_get_mode_ema_profile_fallback(self):
        profile = get_mode_ema_profile("nonexistent_mode")
        default = get_mode_ema_profile("open_dance")
        assert profile.impact.attack_ms == default.impact.attack_ms

    def test_dinner_slower_than_banger_all_lanes(self):
        dinner = get_mode_ema_profile("dinner")
        banger = get_mode_ema_profile("banger")
        for lane in ("impact", "room", "floor", "beam", "sparkle"):
            d_cfg = getattr(dinner, lane)
            b_cfg = getattr(banger, lane)
            assert d_cfg.attack_ms >= b_cfg.attack_ms, (
                f"dinner.{lane}.attack_ms ({d_cfg.attack_ms}) should be >= "
                f"banger.{lane}.attack_ms ({b_cfg.attack_ms})"
            )


# ---------------------------------------------------------------------------
# 5. Alpha coefficient accuracy
# ---------------------------------------------------------------------------

class TestAlphaAccuracy:
    """Verify alpha converges correctly over multiple frames."""

    def test_63_percent_rise_in_one_tau(self):
        """EMA should reach ~63.2% of target after one time constant."""
        tau_ms = 100.0
        f = _make_follower(attack_ms=tau_ms, decay_ms=1000)
        n_frames = int((tau_ms / 1000.0) / DT_S)  # frames in one tau
        outputs = _run_frames(f, 1.0, n_frames)
        final = outputs[-1]
        expected = 1.0 - math.exp(-1.0)  # ~0.6321
        assert abs(final - expected) < 0.02, (
            f"After one tau ({n_frames} frames), expected ~{expected:.3f}, got {final:.3f}"
        )

    def test_95_percent_rise_in_three_tau(self):
        """EMA should reach ~95% of target after three time constants."""
        tau_ms = 100.0
        f = _make_follower(attack_ms=tau_ms, decay_ms=5000)
        n_frames = int(3.0 * (tau_ms / 1000.0) / DT_S)
        outputs = _run_frames(f, 1.0, n_frames)
        final = outputs[-1]
        expected = 1.0 - math.exp(-3.0)  # ~0.9502
        assert abs(final - expected) < 0.02, (
            f"After 3τ ({n_frames} frames), expected ~{expected:.3f}, got {final:.3f}"
        )

    def test_decay_symmetry_with_rise(self):
        """
        Decay from 1→0 should mirror rise from 0→1 when using the same tau.
        After one tau of decay, value should be at ~36.8% (1 - 63.2%).
        """
        tau_ms = 100.0
        f = _make_follower(attack_ms=5, decay_ms=tau_ms, initial=1.0)
        n_frames = int((tau_ms / 1000.0) / DT_S)
        outputs = _run_frames(f, 0.0, n_frames)
        final = outputs[-1]
        expected = math.exp(-1.0)  # ~0.3679
        assert abs(final - expected) < 0.02, (
            f"After one decay tau ({n_frames} frames), expected ~{expected:.3f}, "
            f"got {final:.3f}"
        )


# ---------------------------------------------------------------------------
# 6. LaneSmoother integration
# ---------------------------------------------------------------------------

class TestLaneSmootherIntegration:
    def test_default_mode_key(self):
        s = LaneSmoother()
        assert s.impact.config.attack_ms == 10  # open_dance default

    def test_constructor_with_mode(self):
        s = LaneSmoother("banger")
        assert s.impact.config.attack_ms == 5

    def test_update_returns_all_lanes(self):
        s = LaneSmoother()
        s.reset_all(now=0.0)
        result = s.update({"low_energy": 0.5, "mid_energy": 0.3,
                           "high_energy": 0.2, "overall_energy": 0.4}, now=DT_S)
        assert set(result.keys()) == {"impact", "room", "floor", "beam", "sparkle"}
        for v in result.values():
            assert 0.0 <= v <= 1.0
