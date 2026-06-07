"""
Tests for PaletteBlender beat-trigger safeguards.

Validates three constraints that prevent color vomit on syncopated tracks:
  1. Beat cooldown: after a beat-triggered swap, further beat triggers are
     locked out for _BEAT_COOLDOWN_S seconds.
  2. Energy gate: beat triggers are ignored when room energy is below
     _ENERGY_GATE.
  3. Manual override: set_palette() (mode switch) resets the cooldown so
     the new palette responds immediately.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine.palettes import (
    HSVColor,
    Palette,
    PaletteBlender,
    _BEAT_COOLDOWN_S,
    _ENERGY_GATE,
    _HOLDING,
    _TRANSITIONING,
)


def _make_palette(change_rule: str = "energy_trigger",
                  n_colors: int = 4) -> Palette:
    colors = [
        HSVColor(h=i * (360.0 / n_colors), s=1.0, v=1.0, name=f"c{i}")
        for i in range(n_colors)
    ]
    return Palette(
        name="test", colors=colors, transition_ms=1000.0,
        change_rule=change_rule,
    )


def _make_blender(change_rule: str = "energy_trigger",
                  hold_ms: float = 5000.0,
                  now: float = 0.0) -> PaletteBlender:
    p = _make_palette(change_rule=change_rule)
    b = PaletteBlender(p, hold_ms=hold_ms)
    b.reset_time(now)
    b._last_time = now
    return b


# ---------------------------------------------------------------------------
# 1. Beat cooldown
# ---------------------------------------------------------------------------

class TestBeatCooldown:
    def test_first_beat_triggers_transition(self):
        b = _make_blender(now=0.0)
        assert b._state == _HOLDING
        b.update(energy=0.5, beat_trigger=True, now=0.025)
        assert b._state == _TRANSITIONING

    def test_second_beat_within_cooldown_is_blocked(self):
        b = _make_blender(now=0.0)
        # First beat triggers
        b.update(energy=0.5, beat_trigger=True, now=0.025)
        assert b._state == _TRANSITIONING

        # Advance past transition into next HOLDING state
        # (transition_ms=1000, so 1.5s gets us through)
        t = 0.025
        for _ in range(60):
            t += 0.025
            b.update(energy=0.5, beat_trigger=False, now=t)
        assert b._state == _HOLDING, "Should be holding after transition completes"

        # Second beat within cooldown — must NOT trigger
        b.update(energy=0.5, beat_trigger=True, now=t + 0.025)
        assert b._state == _HOLDING, (
            "Beat within cooldown should not trigger transition"
        )

    def test_beat_after_cooldown_expires_triggers(self):
        b = _make_blender(now=0.0)
        # First beat
        b.update(energy=0.5, beat_trigger=True, now=0.025)

        # Fast-forward past transition + cooldown
        t = _BEAT_COOLDOWN_S + 2.0
        for _ in range(80):
            t += 0.025
            b.update(energy=0.5, beat_trigger=False, now=t)

        # Should be back in HOLDING (hold_ms=5000, we've been updating long enough)
        # Force into holding if not already
        if b._state != _HOLDING:
            # Keep advancing
            for _ in range(200):
                t += 0.025
                b.update(energy=0.5, beat_trigger=False, now=t)

        assert b._state == _HOLDING
        b.update(energy=0.5, beat_trigger=True, now=t + 0.025)
        assert b._state == _TRANSITIONING, (
            "Beat after cooldown expiry should trigger transition"
        )

    def test_cooldown_constant_is_reasonable(self):
        assert 5.0 <= _BEAT_COOLDOWN_S <= 20.0, (
            f"Cooldown should be 5-20s for professional look, got {_BEAT_COOLDOWN_S}"
        )

    def test_hold_timer_still_works_during_cooldown(self):
        """Natural hold_ms expiry should still trigger transition even during cooldown."""
        b = _make_blender(hold_ms=1000.0, now=0.0)
        # First beat triggers (starts cooldown)
        b.update(energy=0.5, beat_trigger=True, now=0.025)
        assert b._state == _TRANSITIONING

        # Complete the transition into next hold
        t = 0.025
        for _ in range(60):
            t += 0.025
            b.update(energy=0.5, beat_trigger=False, now=t)

        if b._state == _HOLDING:
            # Advance past hold_ms (1000ms) without beat trigger
            for _ in range(60):
                t += 0.025
                b.update(energy=0.5, beat_trigger=False, now=t)
            # Should have transitioned via natural hold expiry, not beat
            # (we're still within cooldown)
            assert b._state == _TRANSITIONING, (
                "Natural hold_ms expiry should still work during beat cooldown"
            )


# ---------------------------------------------------------------------------
# 2. Energy gate
# ---------------------------------------------------------------------------

class TestEnergyGate:
    def test_beat_ignored_below_energy_gate(self):
        b = _make_blender(now=0.0)
        b.update(energy=_ENERGY_GATE - 0.01, beat_trigger=True, now=0.025)
        assert b._state == _HOLDING, (
            "Beat trigger should be ignored when energy is below gate"
        )

    def test_beat_triggers_at_energy_gate(self):
        b = _make_blender(now=0.0)
        b.update(energy=_ENERGY_GATE, beat_trigger=True, now=0.025)
        assert b._state == _TRANSITIONING

    def test_beat_triggers_above_energy_gate(self):
        b = _make_blender(now=0.0)
        b.update(energy=0.8, beat_trigger=True, now=0.025)
        assert b._state == _TRANSITIONING

    def test_energy_gate_constant_is_reasonable(self):
        assert 0.1 <= _ENERGY_GATE <= 0.5, (
            f"Energy gate should be 0.1-0.5, got {_ENERGY_GATE}"
        )

    def test_quiet_breakdown_never_triggers(self):
        """Simulate 20 seconds of quiet audio with constant beat triggers."""
        b = _make_blender(now=0.0)
        t = 0.0
        transitions = 0
        for _ in range(800):  # 800 frames = 20s at 40Hz
            t += 0.025
            prev_state = b._state
            b.update(energy=0.05, beat_trigger=True, now=t)
            if prev_state == _HOLDING and b._state == _TRANSITIONING:
                transitions += 1
        # Only natural hold_ms expiry should cause transitions, not beats
        # With hold_ms=5000ms over 20s, expect ~3-4 natural transitions
        # (energy gate should block all beat triggers)
        assert transitions <= 5, (
            f"Got {transitions} transitions during quiet audio — "
            f"energy gate should block beat triggers"
        )


# ---------------------------------------------------------------------------
# 3. Manual override
# ---------------------------------------------------------------------------

class TestManualOverride:
    def test_set_palette_resets_cooldown(self):
        b = _make_blender(now=0.0)
        # Trigger first beat (starts cooldown)
        b.update(energy=0.5, beat_trigger=True, now=0.025)
        assert b._last_beat_swap > 0

        # Switch palette (simulates mode button press)
        new_palette = _make_palette()
        b.set_palette(new_palette, now=1.0)
        assert b._last_beat_swap == -_BEAT_COOLDOWN_S, (
            "set_palette must reset beat cooldown"
        )

        # Next beat should trigger immediately despite being within
        # the original cooldown window
        b.update(energy=0.5, beat_trigger=True, now=1.025)
        assert b._state == _TRANSITIONING

    def test_set_palette_resets_state_to_holding(self):
        b = _make_blender(now=0.0)
        b.update(energy=0.5, beat_trigger=True, now=0.025)
        assert b._state == _TRANSITIONING

        new_palette = _make_palette()
        b.set_palette(new_palette, now=1.0)
        assert b._state == _HOLDING

    def test_set_palette_resets_color_index(self):
        b = _make_blender(now=0.0)
        # Advance a few colors
        b.update(energy=0.5, beat_trigger=True, now=0.025)
        t = 0.025
        for _ in range(200):
            t += 0.025
            b.update(energy=0.5, beat_trigger=False, now=t)

        b.set_palette(_make_palette(), now=t + 0.025)
        assert b._color_idx == 0


# ---------------------------------------------------------------------------
# 4. Change rule filtering
# ---------------------------------------------------------------------------

class TestChangeRuleFiltering:
    def test_slow_blend_ignores_beat_trigger(self):
        b = _make_blender(change_rule="slow_blend", now=0.0)
        b.update(energy=0.8, beat_trigger=True, now=0.025)
        assert b._state == _HOLDING, (
            "slow_blend palette should ignore beat triggers"
        )

    def test_none_rule_ignores_beat_trigger(self):
        b = _make_blender(change_rule="none", now=0.0)
        b.update(energy=0.8, beat_trigger=True, now=0.025)
        assert b._state == _HOLDING

    def test_energy_trigger_responds_to_beat(self):
        b = _make_blender(change_rule="energy_trigger", now=0.0)
        b.update(energy=0.5, beat_trigger=True, now=0.025)
        assert b._state == _TRANSITIONING

    def test_fast_beat_responds_to_beat(self):
        b = _make_blender(change_rule="fast_beat", now=0.0)
        b.update(energy=0.5, beat_trigger=True, now=0.025)
        assert b._state == _TRANSITIONING


# ---------------------------------------------------------------------------
# 5. Integration: color vomit prevention
# ---------------------------------------------------------------------------

class TestColorVomitPrevention:
    def test_rapid_beats_cause_at_most_one_swap(self):
        """Simulate 5 seconds of constant beat triggers — only one swap allowed."""
        b = _make_blender(now=0.0)
        t = 0.0
        beat_swaps = 0
        for _ in range(200):  # 200 frames = 5s at 40Hz
            t += 0.025
            was_holding = b._state == _HOLDING
            b.update(energy=0.6, beat_trigger=True, now=t)
            if was_holding and b._state == _TRANSITIONING:
                # Check if this was a beat-triggered swap (not hold expiry)
                if b._last_beat_swap == t:
                    beat_swaps += 1
        assert beat_swaps == 1, (
            f"Expected exactly 1 beat-triggered swap in 5s, got {beat_swaps}"
        )

    def test_syncopated_beats_do_not_cause_rapid_cycling(self):
        """
        Simulate a Latin track: irregular beats at 0.3s, 0.7s, 0.4s, 0.8s intervals.
        Must not produce more than 1 beat-triggered color swap in the first 10s.
        """
        b = _make_blender(change_rule="energy_trigger", hold_ms=4000.0, now=0.0)
        t = 0.0
        beat_swaps = 0
        beat_times = []
        # Generate irregular beat pattern
        intervals = [0.3, 0.7, 0.4, 0.8, 0.5, 0.6, 0.35, 0.65, 0.45, 0.55]
        beat_t = 0.1
        for ivl in intervals * 3:  # repeat pattern 3x
            beat_t += ivl
            beat_times.append(beat_t)

        frame = 0
        while t < 10.0:
            t += 0.025
            frame += 1
            is_beat = any(abs(t - bt) < 0.013 for bt in beat_times)
            was_holding = b._state == _HOLDING
            b.update(energy=0.5, beat_trigger=is_beat, now=t)
            if was_holding and b._state == _TRANSITIONING and b._last_beat_swap == t:
                beat_swaps += 1

        assert beat_swaps <= 1, (
            f"Syncopated Latin beat pattern caused {beat_swaps} beat-triggered "
            f"swaps in 10s — should be at most 1"
        )
