"""
Scene layout for the LightBrain Preview Visualizer.

Defines the 2D positions of all fixtures in the mobile DJ rig and maps
the lighting engine's output to a RigVisualState that the visualizer can render.

Current rig:
  - 18 RockWedge uplights around the dance floor perimeter
  -  2 Chauvet Wash FX (flanking the DJ booth)
  -  1 Chauvet GigBAR Move + ILS (center, above DJ booth)
  -  2 DJFLX moving beams (outer flanks)
"""

import colorsys
import math
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from app.render.fixture_state import (
    UplightState, WashState, BeamState, SparkleState, ImpactState,
    RigVisualState, RGB,
)


# ---------------------------------------------------------------------------
# Window constants
# ---------------------------------------------------------------------------

WINDOW_W = 1200
WINDOW_H = 800

# Dance floor bounding rect
FLOOR_L, FLOOR_T, FLOOR_R, FLOOR_B = 130, 65, 1070, 545

# DJ booth
BOOTH_L, BOOTH_T, BOOTH_R, BOOTH_B = 430, 620, 770, 700


# ---------------------------------------------------------------------------
# Per-mode behavior scales
# ---------------------------------------------------------------------------

# Hue offset between uplight zones (top/bottom/left/right walls).
# Larger values make the floor look more dramatically multi-colored.
_ZONE_OFFSET: dict = {
    "dinner":       12.0,
    "speech":        0.0,   # uniform: neutral white look
    "open_dance":   35.0,
    "banger":       50.0,
    "indian_latin": 40.0,
    "slow_dance":   18.0,
    "blackout":      0.0,
}


def _uplight_zone_hue(i: int, base_hue: float, zone_offset: float) -> float:
    """Return hue for uplight i: top/bottom/left/right walls get distinct offsets."""
    if i < 6:
        return base_hue                            # top wall
    elif i < 12:
        return (base_hue + zone_offset) % 360      # bottom wall
    elif i < 15:
        return (base_hue - zone_offset * 0.5) % 360  # left wall
    else:
        return (base_hue + zone_offset * 0.5) % 360  # right wall


# How much sparkle each mode shows (0 = none, 1 = full)
_SPARKLE_SCALE: dict = {
    "dinner":      0.0,
    "speech":      0.0,
    "open_dance":  0.45,
    "banger":      1.0,
    "indian_latin": 0.8,
    "slow_dance":  0.1,
    "blackout":    0.0,
}

# Beam movement speed multiplier
_BEAM_SPEED_SCALE: dict = {
    "dinner":      0.25,
    "speech":      0.1,
    "open_dance":  0.65,
    "banger":      1.0,
    "indian_latin": 0.85,
    "slow_dance":  0.2,
    "blackout":    0.0,
}

# Modes where the impact/strobe flash is visible
_FLASH_MODES = {"open_dance", "banger", "indian_latin"}

# Uplight brightness floor per mode (some modes keep lights dimmer overall)
_UPLIGHT_DIM: dict = {
    "dinner":      0.6,
    "speech":      0.5,
    "open_dance":  1.0,
    "banger":      1.0,
    "indian_latin": 1.0,
    "slow_dance":  0.7,
    "blackout":    0.0,
}


# ---------------------------------------------------------------------------
# Uplight positions (18 total)
# ---------------------------------------------------------------------------

def _uplight_positions() -> List[Tuple[float, float]]:
    """Return (x, y) screen positions for 18 uplights around the dance floor."""
    positions = []

    # Top wall (6 uplights)
    xs_top = [int(FLOOR_L + (FLOOR_R - FLOOR_L) * (i + 1) / 7) for i in range(6)]
    for x in xs_top:
        positions.append((x, FLOOR_T - 22))

    # Bottom wall (6 uplights)
    xs_bot = [int(FLOOR_L + (FLOOR_R - FLOOR_L) * (i + 1) / 7) for i in range(6)]
    for x in xs_bot:
        positions.append((x, FLOOR_B + 22))

    # Left wall (3 uplights)
    ys_left = [int(FLOOR_T + (FLOOR_B - FLOOR_T) * (i + 1) / 4) for i in range(3)]
    for y in ys_left:
        positions.append((FLOOR_L - 22, y))

    # Right wall (3 uplights)
    ys_right = [int(FLOOR_T + (FLOOR_B - FLOOR_T) * (i + 1) / 4) for i in range(3)]
    for y in ys_right:
        positions.append((FLOOR_R + 22, y))

    return positions  # 6 + 6 + 3 + 3 = 18


_UPLIGHT_POS = _uplight_positions()

# Chauvet Wash FX2 positions — mounted on front speakers flanking the DJ
# (wide spread so wash covers the full floor from the back corners)
_WASH_POS = [
    (175, 655),    # left front speaker / speaker stand
    (1025, 655),   # right front speaker / speaker stand
]

# GigBAR Move+ILS — center, behind/beside DJ
_GIGBAR_POS = (600, 660)

# DJFLX beam positions (visualizer-only, no DMX) — flanking the booth
_BEAM_POS = [
    (340, 665),    # inner left
    (860, 665),    # inner right
]


# ---------------------------------------------------------------------------
# Scene layout and state machine
# ---------------------------------------------------------------------------

def _hsv_to_rgb(h_deg: float, s: float, v: float) -> RGB:
    """Convert HSV (h=0-360, s=0-1, v=0-1) to integer RGB tuple."""
    r, g, b = colorsys.hsv_to_rgb(h_deg / 360.0, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


class SceneLayout:
    """
    Maintains scene-level animation state (beam sweep angles, etc.) and
    builds a RigVisualState snapshot from the current engine output each frame.
    """

    def __init__(self):
        self._beam_phase  = 0.0        # driving sine for beam sweep
        self._last_update = time.monotonic()

    def reset_time(self, now: float) -> None:
        """Reset internal clock and beam phase for deterministic replay (Sprint 3)."""
        self._last_update = now
        self._beam_phase  = 0.0

    def update_and_build(
        self,
        bands:          dict,    # raw band energies
        lanes:          dict,    # smoothed lanes: impact, room, ...
        hue:            float,   # 0–360 current palette hue
        saturation:     float,   # 0–1 after saturation_scale
        brightness:     float,   # 0–1 final brightness
        base_brt:       float,   # brightness floor contribution
        pulse_brt:      float,   # bass pulse contribution
        mode_key:       str,
        palette_name:   str,
        blackout:       bool,
        now:            Optional[float] = None,   # Sprint 3: inject clock
        strobe_on:      bool  = False,            # EDM rise strobe oscillator on-phase
        strobe_rate:    float = 0.0,              # 0–1 strobe speed/intensity
        ambient_white:  float = 0.0,              # W channel level for canvas warmth
        ambient_amber:  float = 0.0,              # A channel level for canvas warmth
    ) -> RigVisualState:
        """Build a complete RigVisualState for one render frame.

        now — optional clock override for deterministic replay (Sprint 3);
              pass frame.time_s from AnalysisTimeline for deterministic beam animation.
        """
        if now is None:
            now = time.monotonic()
        dt  = min(now - self._last_update, 0.1)
        self._last_update = now

        impact = lanes.get("impact", 0.0)
        room   = lanes.get("room",   0.0)

        # Advance beam sweep
        beam_speed = _BEAM_SPEED_SCALE.get(mode_key, 0.5)
        self._beam_phase += dt * beam_speed * 0.8

        # Pure hue color (no brightness baked in — brightness controls glow)
        main_rgb   = _hsv_to_rgb(hue, saturation, 1.0)
        # Complementary hue for beams (180° shift adds visual contrast)
        beam_hue   = (hue + 160) % 360
        beam_rgb   = _hsv_to_rgb(beam_hue, min(1.0, saturation * 0.9), 1.0)

        # Per-mode parameters
        sparkle_scale  = _SPARKLE_SCALE.get(mode_key, 0.0)
        uplight_dim    = _UPLIGHT_DIM.get(mode_key, 1.0)
        flash_allowed  = mode_key in _FLASH_MODES

        high_e  = bands.get("high_energy", 0.0)
        overall = bands.get("overall_energy", 0.0)

        # Effective uplight brightness
        eff_brightness = brightness * uplight_dim

        # ------------------------------------------------------------------
        # Uplights (18) — 4 wall zones with mode-dependent hue offsets
        # ------------------------------------------------------------------
        zone_offset = _ZONE_OFFSET.get(mode_key, 0.0)
        uplights = [
            UplightState(
                fixture_id=f"rw_{i:02d}",
                x=float(pos[0]),
                y=float(pos[1]),
                color_rgb=_hsv_to_rgb(
                    _uplight_zone_hue(i, hue, zone_offset), saturation, 1.0
                ),
                brightness=eff_brightness,
                active=not blackout,
            )
            for i, pos in enumerate(_UPLIGHT_POS)
        ]

        # ------------------------------------------------------------------
        # Washes (2 Chauvet Wash FX)
        # ------------------------------------------------------------------
        wash_base_radius = 180.0
        washes = [
            WashState(
                fixture_id=f"wash_{i:02d}",
                x=float(pos[0]),
                y=float(pos[1]),
                color_rgb=main_rgb,
                brightness=eff_brightness * 0.85,
                radius=wash_base_radius,
                pulse_strength=impact,
                active=not blackout,
            )
            for i, pos in enumerate(_WASH_POS)
        ]

        # GigBAR central wash (smaller, overhead)
        washes.append(WashState(
            fixture_id="gigbar_wash",
            x=float(_GIGBAR_POS[0]),
            y=float(_GIGBAR_POS[1]),
            color_rgb=main_rgb,
            brightness=eff_brightness * 0.7,
            radius=120.0,
            pulse_strength=impact * 0.5,
            active=not blackout,
        ))

        # ------------------------------------------------------------------
        # Beams: 2 DJFLX (outer) + 2 GigBAR movers (inner)
        # ------------------------------------------------------------------
        # DJFLX: mirror-symmetric sweep so both converge/diverge together
        sweep_l = -35.0 + math.sin(self._beam_phase) * 22.0
        sweep_r =  35.0 - math.sin(self._beam_phase) * 22.0

        # GigBAR movers: faster phase, smaller range, project up into floor
        gigbar_phase = self._beam_phase * 1.4 + 0.8
        gigbar_sweep_l =  math.sin(gigbar_phase) * 28.0
        gigbar_sweep_r = -math.sin(gigbar_phase) * 28.0

        beam_len = 400.0 + impact * 80.0

        # GigBAR mover head positions: ±27.5 px from bar centre on the 110-wide bar
        _gb_y = float(_GIGBAR_POS[1])

        beams = [
            BeamState(
                fixture_id="beam_l",
                x=float(_BEAM_POS[0][0]),
                y=float(_BEAM_POS[0][1]),
                color_rgb=beam_rgb,
                brightness=eff_brightness * 0.9,
                angle_degrees=sweep_l,
                length=beam_len,
                spread=6.0,
                movement_speed=beam_speed,
                active=not blackout,
            ),
            BeamState(
                fixture_id="beam_r",
                x=float(_BEAM_POS[1][0]),
                y=float(_BEAM_POS[1][1]),
                color_rgb=beam_rgb,
                brightness=eff_brightness * 0.9,
                angle_degrees=sweep_r,
                length=beam_len,
                spread=6.0,
                movement_speed=beam_speed,
                active=not blackout,
            ),
            BeamState(
                fixture_id="gigbar_mover_l",
                x=float(_GIGBAR_POS[0] - 27.5),
                y=_gb_y,
                color_rgb=main_rgb,
                brightness=eff_brightness * 0.8,
                angle_degrees=gigbar_sweep_l,
                length=280.0 + impact * 40.0,
                spread=4.0,
                movement_speed=beam_speed * 1.4,
                active=not blackout,
            ),
            BeamState(
                fixture_id="gigbar_mover_r",
                x=float(_GIGBAR_POS[0] + 27.5),
                y=_gb_y,
                color_rgb=main_rgb,
                brightness=eff_brightness * 0.8,
                angle_degrees=gigbar_sweep_r,
                length=280.0 + impact * 40.0,
                spread=4.0,
                movement_speed=beam_speed * 1.4,
                active=not blackout,
            ),
        ]

        # ------------------------------------------------------------------
        # Sparkle (GigBAR ILS derby effect)
        # ------------------------------------------------------------------
        sparkle_amount = high_e * sparkle_scale * (0.5 + room * 0.5)
        sparkles = [
            SparkleState(
                fixture_id="gigbar_derby",
                x=float(_GIGBAR_POS[0]),
                y=float(_GIGBAR_POS[1]),
                color_rgb=main_rgb,
                brightness=eff_brightness,
                sparkle_amount=sparkle_amount,
                active=not blackout,
            )
        ]

        # ------------------------------------------------------------------
        # Impact / flash (GigBAR strobe head)
        # ------------------------------------------------------------------
        # Flash fires on bass impact OR during EDM rise strobe pulse
        flash_on  = flash_allowed and (impact > 0.75 or strobe_on)
        flash_brt = max(impact, strobe_rate * 0.65) if flash_on else impact
        impacts = [
            ImpactState(
                fixture_id="gigbar_impact",
                x=float(_GIGBAR_POS[0]),
                y=float(_GIGBAR_POS[1]),
                brightness=flash_brt,
                flash_active=flash_on,
                active=not blackout,
            )
        ]

        ambient_warm = min(1.0, ambient_amber + ambient_white * 0.5) if not blackout else 0.0

        return RigVisualState(
            mode=mode_key,
            palette_name=palette_name,
            low_energy=bands.get("low_energy", 0.0),
            mid_energy=bands.get("mid_energy", 0.0),
            high_energy=high_e,
            overall_energy=overall,
            room_brightness=brightness,
            impact_value=impact,
            uplights=uplights,
            washes=washes,
            beams=beams,
            sparkles=sparkles,
            impacts=impacts,
            blackout_active=blackout,
            ambient_warm=ambient_warm,
        )
