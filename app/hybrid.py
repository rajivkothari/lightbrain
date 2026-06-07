"""
HybridEngine — blends a saved FixtureStateTimeline with live audio engine output.

In hybrid mode the saved program provides structural lighting decisions
(color, palette transitions, phrase timing) while the live audio still drives
brightness and impact pulses, so drops still hit even if the song loops or is
mixed differently than during the preview.

Blend parameter (0.0–1.0):
  0.0 = 100 % live audio output  (program is ignored)
  0.5 = equal mix                (default — colour from program, brightness from live)
  1.0 = 100 % program replay     (live audio is ignored)

Usage:
    hybrid = HybridEngine(saved_program.fixture_state_timeline, blend=0.5)
    hybrid.play()

    # In the render loop (at live-audio frame rate):
    live_state  = scene.update_and_build(...)   # from the live engine
    final_state = hybrid.blend_with_live(live_state)
    visualizer.draw(screen, final_state)
"""

import time
from typing import Optional

from app.render.fixture_state import (
    FixtureStateTimeline, RigVisualState,
    UplightState, WashState, BeamState, SparkleState, ImpactState,
)
from app.render.playback import PlaybackController


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class HybridEngine:
    """
    Drives a FixtureStateTimeline at wall-clock speed while blending its
    output with a live RigVisualState produced by the real-time engine.
    """

    def __init__(
        self,
        program_timeline: FixtureStateTimeline,
        blend: float = 0.5,
    ):
        """
        program_timeline — pre-generated FixtureStateTimeline from LightingProgram
        blend            — 0.0 = pure live, 1.0 = pure program (default 0.5)
        """
        self._controller = PlaybackController()
        self._controller.load(program_timeline)
        self._blend      = max(0.0, min(1.0, blend))

    # ------------------------------------------------------------------
    # Playback controls (delegate to the internal PlaybackController)
    # ------------------------------------------------------------------

    def play(self)  -> None: self._controller.play()
    def pause(self) -> None: self._controller.pause()

    def toggle_play_pause(self) -> None:
        self._controller.toggle_play_pause()

    def seek(self, time_s: float) -> None:
        self._controller.seek(time_s)

    def rewind(self) -> None:
        self._controller.rewind()

    # ------------------------------------------------------------------
    # Blend configuration
    # ------------------------------------------------------------------

    def set_blend(self, blend: float) -> None:
        """Set the live/program blend ratio (clamped to 0.0–1.0)."""
        self._blend = max(0.0, min(1.0, blend))

    # ------------------------------------------------------------------
    # Per-frame update
    # ------------------------------------------------------------------

    def blend_with_live(
        self,
        live_state: RigVisualState,
    ) -> RigVisualState:
        """
        Advance the program playhead by wall-clock time, then return a
        RigVisualState blended between live and program output.

        live_state — RigVisualState produced by the live engine this frame.
        """
        program_state = self._controller.update()

        if program_state is None or self._blend <= 0.0:
            return live_state
        if self._blend >= 1.0:
            return program_state

        return blend_rig_states(live_state, program_state, self._blend)

    # ------------------------------------------------------------------
    # State accessors
    # ------------------------------------------------------------------

    @property
    def blend(self) -> float:
        return self._blend

    @property
    def current_time_s(self) -> float:
        return self._controller.current_time_s

    @property
    def duration_s(self) -> float:
        return self._controller.duration_s

    @property
    def is_playing(self) -> bool:
        return self._controller.is_playing

    @property
    def progress(self) -> float:
        return self._controller.progress


# ---------------------------------------------------------------------------
# State blending
# ---------------------------------------------------------------------------

def blend_rig_states(
    live:    RigVisualState,
    program: RigVisualState,
    t:       float,
) -> RigVisualState:
    """
    Linearly interpolate between two RigVisualStates.

    t=0 → pure live, t=1 → pure program.

    Scalar fields (brightness, energy, impact) are lerped.
    RGB colors are lerped per channel.
    Fixture lists are blended element-wise when counts match;
    when counts differ the majority-side list is used.
    Blackout propagates if either source has it active.
    """
    def lerp(a: float, b: float) -> float:
        return a * (1.0 - t) + b * t

    def lerp_rgb(a, b):
        return tuple(int(ca * (1.0 - t) + cb * t) for ca, cb in zip(a, b))

    dominant = program if t >= 0.5 else live

    uplights = _blend_uplights(live.uplights, program.uplights, t, lerp, lerp_rgb)
    washes   = _blend_washes(  live.washes,   program.washes,   t, lerp, lerp_rgb)
    beams    = _blend_beams(   live.beams,    program.beams,    t, lerp, lerp_rgb)
    sparkles = _blend_sparkles(live.sparkles, program.sparkles, t, lerp, lerp_rgb)
    impacts  = _blend_impacts( live.impacts,  program.impacts,  t, lerp)

    return RigVisualState(
        mode=          dominant.mode,
        palette_name=  dominant.palette_name,
        low_energy=    lerp(live.low_energy,      program.low_energy),
        mid_energy=    lerp(live.mid_energy,       program.mid_energy),
        high_energy=   lerp(live.high_energy,      program.high_energy),
        overall_energy=lerp(live.overall_energy,   program.overall_energy),
        room_brightness=lerp(live.room_brightness, program.room_brightness),
        impact_value=  lerp(live.impact_value,     program.impact_value),
        uplights=  uplights,
        washes=    washes,
        beams=     beams,
        sparkles=  sparkles,
        impacts=   impacts,
        blackout_active=live.blackout_active or program.blackout_active,
    )


def _same_length_or_dominant(live_list, prog_list, t):
    """Return (same_length, dominant_list) for fixture list blending."""
    if len(live_list) == len(prog_list):
        return True, None
    return False, (prog_list if t >= 0.5 else live_list)


def _blend_uplights(live_list, prog_list, t, lerp, lerp_rgb):
    same, dominant = _same_length_or_dominant(live_list, prog_list, t)
    if not same:
        return dominant
    return [
        UplightState(
            fixture_id=pu.fixture_id,
            x=pu.x, y=pu.y,
            color_rgb=lerp_rgb(lu.color_rgb, pu.color_rgb),
            brightness=lerp(lu.brightness, pu.brightness),
            active=(pu if t >= 0.5 else lu).active,
        )
        for lu, pu in zip(live_list, prog_list)
    ]


def _blend_washes(live_list, prog_list, t, lerp, lerp_rgb):
    same, dominant = _same_length_or_dominant(live_list, prog_list, t)
    if not same:
        return dominant
    return [
        WashState(
            fixture_id=pw.fixture_id,
            x=pw.x, y=pw.y,
            color_rgb=lerp_rgb(lw.color_rgb, pw.color_rgb),
            brightness=lerp(lw.brightness, pw.brightness),
            radius=lerp(lw.radius, pw.radius),
            pulse_strength=lerp(lw.pulse_strength, pw.pulse_strength),
            active=(pw if t >= 0.5 else lw).active,
        )
        for lw, pw in zip(live_list, prog_list)
    ]


def _blend_beams(live_list, prog_list, t, lerp, lerp_rgb):
    same, dominant = _same_length_or_dominant(live_list, prog_list, t)
    if not same:
        return dominant
    return [
        BeamState(
            fixture_id=pb.fixture_id,
            x=pb.x, y=pb.y,
            color_rgb=lerp_rgb(lb.color_rgb, pb.color_rgb),
            brightness=lerp(lb.brightness, pb.brightness),
            angle_degrees=lerp(lb.angle_degrees, pb.angle_degrees),
            length=lerp(lb.length, pb.length),
            spread=lerp(lb.spread, pb.spread),
            movement_speed=lerp(lb.movement_speed, pb.movement_speed),
            active=(pb if t >= 0.5 else lb).active,
        )
        for lb, pb in zip(live_list, prog_list)
    ]


def _blend_sparkles(live_list, prog_list, t, lerp, lerp_rgb):
    same, dominant = _same_length_or_dominant(live_list, prog_list, t)
    if not same:
        return dominant
    return [
        SparkleState(
            fixture_id=ps.fixture_id,
            x=ps.x, y=ps.y,
            color_rgb=lerp_rgb(ls.color_rgb, ps.color_rgb),
            brightness=lerp(ls.brightness, ps.brightness),
            sparkle_amount=lerp(ls.sparkle_amount, ps.sparkle_amount),
            active=(ps if t >= 0.5 else ls).active,
        )
        for ls, ps in zip(live_list, prog_list)
    ]


def _blend_impacts(live_list, prog_list, t, lerp):
    same, dominant = _same_length_or_dominant(live_list, prog_list, t)
    if not same:
        return dominant
    return [
        ImpactState(
            fixture_id=pi.fixture_id,
            x=pi.x, y=pi.y,
            brightness=lerp(li.brightness, pi.brightness),
            flash_active=li.flash_active or pi.flash_active,
            active=(pi if t >= 0.5 else li).active,
        )
        for li, pi in zip(live_list, prog_list)
    ]
