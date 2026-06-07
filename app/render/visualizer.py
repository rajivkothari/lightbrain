"""
Pygame-based 2D visualizer for LightBrain.

Renders the RigVisualState onto a pygame surface. All drawing functions
use BLEND_RGBA_ADD for additive light blending — overlapping glow areas
become brighter, which matches real light physics.

Architecture:
  Visualizer.draw(surface, rig_state, fps) — main entry point, renders
  one frame of the full rig preview onto the given surface.
"""

import math
import random
import time
from typing import List, Optional, Tuple

import pygame

from app.render.fixture_state import (
    UplightState, WashState, BeamState, SparkleState, ImpactState,
    RigVisualState, RGB,
)
from app.render.scene import (
    WINDOW_W, WINDOW_H,
    FLOOR_L, FLOOR_T, FLOOR_R, FLOOR_B,
    BOOTH_L, BOOTH_T, BOOTH_R, BOOTH_B,
)


# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------

_BG_COLOR      = (10,  10,  15)
_FLOOR_COLOR   = (22,  22,  30)
_FLOOR_EDGE    = (45,  45,  60)
_BOOTH_COLOR   = (30,  25,  20)
_BOOTH_EDGE    = (70,  60,  40)
_LABEL_COLOR   = (80,  80, 100)
_BLACKOUT_OVL  = (0,   0,   0, 230)
_BLACKOUT_TXT  = (200, 40,  40)
_HUD_BG        = (10,  10,  15, 180)
_HUD_TEXT      = (200, 200, 220)
_HUD_ACCENT    = (80, 220, 160)
_HUD_WARN      = (220, 180,  60)


# ---------------------------------------------------------------------------
# Low-level draw helpers
# ---------------------------------------------------------------------------

def _draw_glow(target: pygame.Surface, x: int, y: int,
               color: RGB, radius: int, brightness: float) -> None:
    """Additive soft glow circle. Draws outer halo then bright core."""
    if brightness < 0.01 or radius < 2:
        return
    r, g, b = color

    # Outer halo — one large alpha circle
    halo_r = int(radius * 1.8)
    halo_a = int(70 * brightness)
    if halo_r > 1 and halo_a > 2:
        hs = pygame.Surface((halo_r * 2, halo_r * 2), pygame.SRCALPHA)
        pygame.draw.circle(hs, (r, g, b, halo_a), (halo_r, halo_r), halo_r)
        target.blit(hs, (x - halo_r, y - halo_r), special_flags=pygame.BLEND_RGBA_ADD)

    # Mid glow
    mid_r = int(radius * 1.1)
    mid_a = int(110 * brightness)
    if mid_r > 1 and mid_a > 2:
        ms = pygame.Surface((mid_r * 2, mid_r * 2), pygame.SRCALPHA)
        pygame.draw.circle(ms, (r, g, b, mid_a), (mid_r, mid_r), mid_r)
        target.blit(ms, (x - mid_r, y - mid_r), special_flags=pygame.BLEND_RGBA_ADD)

    # Bright core — clamp to avoid overflow
    core_r = max(3, int(radius * 0.45 * brightness))
    cr = min(255, r + 80)
    cg = min(255, g + 80)
    cb = min(255, b + 80)
    pygame.draw.circle(target, (cr, cg, cb), (x, y), core_r)


def _draw_wash_circle(target: pygame.Surface, x: int, y: int,
                      color: RGB, radius: float, brightness: float,
                      pulse_strength: float = 0.0) -> None:
    """Translucent wash area. Pulses slightly with bass."""
    if brightness < 0.01:
        return
    r, g, b = color
    eff_r = int(radius * (1.0 + pulse_strength * 0.18))
    if eff_r < 5:
        return

    # Three concentric layers: outer thin halo, mid body, inner core
    for frac, a_scale in [(1.0, 0.18), (0.65, 0.28), (0.35, 0.35)]:
        layer_r = int(eff_r * frac)
        alpha   = int(255 * a_scale * brightness)
        if layer_r < 2 or alpha < 2:
            continue
        ws = pygame.Surface((layer_r * 2, layer_r * 2), pygame.SRCALPHA)
        pygame.draw.circle(ws, (r, g, b, alpha), (layer_r, layer_r), layer_r)
        target.blit(ws, (x - layer_r, y - layer_r), special_flags=pygame.BLEND_RGBA_ADD)


def _draw_beam_cone(target: pygame.Surface, ox: int, oy: int,
                    color: RGB, brightness: float,
                    angle_deg: float, length: float, spread_deg: float) -> None:
    """Additive beam cone from (ox, oy) pointing at angle_deg from vertical."""
    if brightness < 0.02 or length < 10:
        return
    r, g, b = color

    half_spread = math.radians(max(1.0, spread_deg / 2))
    center_rad  = math.radians(angle_deg)

    def tip(rad: float, ln: float) -> Tuple[int, int]:
        return (
            int(ox + math.sin(rad) * ln),
            int(oy - math.cos(rad) * ln),
        )

    # Inner bright beam
    pt_l = tip(center_rad - half_spread, length)
    pt_r = tip(center_rad + half_spread, length)
    alpha_inner = int(200 * brightness)

    bs = pygame.Surface((WINDOW_W, WINDOW_H), pygame.SRCALPHA)
    pygame.draw.polygon(bs, (r, g, b, alpha_inner), [(ox, oy), pt_l, pt_r])
    target.blit(bs, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)

    # Outer soft halo around the beam
    wide = half_spread * 1.8
    pw_l = tip(center_rad - wide, length * 0.9)
    pw_r = tip(center_rad + wide, length * 0.9)
    alpha_outer = int(50 * brightness)
    bs2 = pygame.Surface((WINDOW_W, WINDOW_H), pygame.SRCALPHA)
    pygame.draw.polygon(bs2, (r, g, b, alpha_outer), [(ox, oy), pw_l, pw_r])
    target.blit(bs2, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)


def _draw_sparkles(target: pygame.Surface, rig_state: RigVisualState,
                   sparkle_state: SparkleState, rng: random.Random) -> None:
    """Scatter sparkle dots across the dance floor."""
    if not sparkle_state.active or sparkle_state.brightness < 0.01:
        return
    amount = sparkle_state.sparkle_amount
    if amount < 0.01:
        return

    r, g, b = sparkle_state.color_rgb
    # Cycle through a deterministic but time-varying set of positions
    num_dots = int(amount * 60 + 5)
    fw = FLOOR_R - FLOOR_L
    fh = FLOOR_B - FLOOR_T

    for i in range(num_dots):
        # Seed per-dot so they "flicker" rather than move
        rng.seed(i + int(time.monotonic() * 4) % 1000)
        sx = FLOOR_L + int(rng.random() * fw)
        sy = FLOOR_T + int(rng.random() * fh)
        alpha = int(180 * sparkle_state.brightness * (0.4 + rng.random() * 0.6))
        dot_r = rng.randint(1, 3)
        ds = pygame.Surface((dot_r * 2 + 2, dot_r * 2 + 2), pygame.SRCALPHA)
        pygame.draw.circle(ds, (min(255, r + 100), min(255, g + 100), min(255, b + 100), alpha),
                           (dot_r + 1, dot_r + 1), dot_r)
        target.blit(ds, (sx - dot_r, sy - dot_r), special_flags=pygame.BLEND_RGBA_ADD)


def _draw_impact_flash(target: pygame.Surface, impact: ImpactState) -> None:
    """White flash overlay when impact fires."""
    if not impact.active or not impact.flash_active:
        return
    alpha = int(160 * impact.brightness)
    if alpha < 10:
        return
    flash = pygame.Surface((WINDOW_W, WINDOW_H), pygame.SRCALPHA)
    flash.fill((255, 255, 255, alpha))
    target.blit(flash, (0, 0))


def _draw_blackout(target: pygame.Surface) -> None:
    """Near-black overlay with BLACKOUT label."""
    overlay = pygame.Surface((WINDOW_W, WINDOW_H), pygame.SRCALPHA)
    overlay.fill(_BLACKOUT_OVL)
    target.blit(overlay, (0, 0))


# ---------------------------------------------------------------------------
# Scene structure elements
# ---------------------------------------------------------------------------

def _draw_scene_background(target: pygame.Surface) -> None:
    """Draw the static scene: background, dance floor, DJ booth."""
    target.fill(_BG_COLOR)

    # Dance floor
    floor_rect = pygame.Rect(FLOOR_L, FLOOR_T, FLOOR_R - FLOOR_L, FLOOR_B - FLOOR_T)
    pygame.draw.rect(target, _FLOOR_COLOR, floor_rect)
    pygame.draw.rect(target, _FLOOR_EDGE,  floor_rect, 2)

    # DJ booth
    booth_rect = pygame.Rect(BOOTH_L, BOOTH_T, BOOTH_R - BOOTH_L, BOOTH_B - BOOTH_T)
    pygame.draw.rect(target, _BOOTH_COLOR, booth_rect)
    pygame.draw.rect(target, _BOOTH_EDGE,  booth_rect, 2)


def _draw_fixture_markers(target: pygame.Surface, font: pygame.font.Font) -> None:
    """Draw small dim markers showing fixture positions."""
    cx_gigbar = (BOOTH_L + BOOTH_R) // 2
    cy_booth  = (BOOTH_T + BOOTH_B) // 2

    lbl = font.render("GIGBAR", True, _LABEL_COLOR)
    target.blit(lbl, (cx_gigbar - lbl.get_width() // 2, cy_booth - 6))

    lbl2 = font.render("DJ BOOTH", True, (60, 50, 35))
    target.blit(lbl2, (cx_gigbar - lbl2.get_width() // 2, BOOTH_T + 28))


# ---------------------------------------------------------------------------
# HUD / debug overlay
# ---------------------------------------------------------------------------

def _draw_hud(target: pygame.Surface, rig: RigVisualState,
              fps: float, audio_mode: str, font: pygame.font.Font,
              font_sm: pygame.font.Font) -> None:
    """Compact debug overlay in the top-left corner."""
    lines = [
        (f"MODE: {rig.mode.upper():<12}  PAL: {rig.palette_name}", _HUD_ACCENT),
        (f"AUDIO: {audio_mode:<8}   FPS: {fps:4.0f}", _HUD_TEXT),
        (f"LOW:{rig.low_energy:.2f}  MID:{rig.mid_energy:.2f}  HI:{rig.high_energy:.2f}  OVR:{rig.overall_energy:.2f}", _HUD_TEXT),
        (f"ROOM: {rig.room_brightness:.2f}   IMPACT: {rig.impact_value:.2f}", _HUD_TEXT),
    ]
    if rig.blackout_active:
        lines.insert(0, ("  *** BLACKOUT ***", _BLACKOUT_TXT))

    pad = 8
    line_h = font.get_height() + 3
    total_h = len(lines) * line_h + pad * 2
    total_w = 500

    # Semi-transparent background
    hud_surf = pygame.Surface((total_w, total_h), pygame.SRCALPHA)
    hud_surf.fill(_HUD_BG)
    target.blit(hud_surf, (0, 0))

    for i, (text, color) in enumerate(lines):
        lbl = font.render(text, True, color)
        target.blit(lbl, (pad, pad + i * line_h))

    # Key controls hint (bottom of screen)
    hint = "O=OpenDance  D=Dinner  B=Banger  I=Indian  S=Speech  L=SlowDance  Space=BO  A=Audio  Q=Quit"
    hint_lbl = font_sm.render(hint, True, (80, 80, 100))
    target.blit(hint_lbl, (10, WINDOW_H - hint_lbl.get_height() - 5))


# ---------------------------------------------------------------------------
# Main Visualizer
# ---------------------------------------------------------------------------

class Visualizer:
    """
    Renders a complete RigVisualState onto a pygame surface.

    Usage:
        vis = Visualizer()
        vis.init()            # call once after pygame.init()
        ...
        vis.draw(screen, rig_state, fps, audio_mode)
    """

    def __init__(self):
        self._font:    Optional[pygame.font.Font] = None
        self._font_sm: Optional[pygame.font.Font] = None
        self._font_lg: Optional[pygame.font.Font] = None
        self._rng = random.Random(42)

    def init(self) -> None:
        """Initialise fonts. Call after pygame.init()."""
        pygame.font.init()
        self._font    = pygame.font.SysFont("monospace", 14, bold=False)
        self._font_sm = pygame.font.SysFont("monospace", 11, bold=False)
        self._font_lg = pygame.font.SysFont("monospace", 48, bold=True)

    def draw(self, surface: pygame.Surface, rig: RigVisualState,
             fps: float = 0.0, audio_mode: str = "SIMULATED") -> None:
        """Render one frame of the rig preview onto surface."""

        # 1 — static scene background
        _draw_scene_background(surface)

        if not rig.blackout_active:
            # 2 — washes (behind uplights so uplights appear on top)
            for wash in rig.washes:
                if wash.active:
                    _draw_wash_circle(
                        surface, int(wash.x), int(wash.y),
                        wash.color_rgb, wash.radius,
                        wash.brightness, wash.pulse_strength,
                    )

            # 3 — beams
            for beam in rig.beams:
                if beam.active:
                    _draw_beam_cone(
                        surface, int(beam.x), int(beam.y),
                        beam.color_rgb, beam.brightness,
                        beam.angle_degrees, beam.length, beam.spread,
                    )

            # 4 — uplights (drawn on top of washes)
            for ul in rig.uplights:
                if ul.active:
                    _draw_glow(
                        surface, int(ul.x), int(ul.y),
                        ul.color_rgb, 28, ul.brightness,
                    )

            # 5 — sparkles on dance floor
            for sp in rig.sparkles:
                if sp.active:
                    _draw_sparkles(surface, rig, sp, self._rng)

            # 6 — impact flash
            for imp in rig.impacts:
                _draw_impact_flash(surface, imp)

        else:
            # Blackout overlay
            _draw_blackout(surface)
            if self._font_lg:
                lbl = self._font_lg.render("BLACKOUT", True, _BLACKOUT_TXT)
                cx  = WINDOW_W // 2 - lbl.get_width() // 2
                cy  = WINDOW_H // 2 - lbl.get_height() // 2
                surface.blit(lbl, (cx, cy))

        # 7 — scene labels
        if self._font_sm:
            _draw_fixture_markers(surface, self._font_sm)

        # 8 — HUD overlay
        if self._font:
            _draw_hud(surface, rig, fps, audio_mode, self._font, self._font_sm)
