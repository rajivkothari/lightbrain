"""
Palette engine — loads HSV color palettes from JSON and provides
smooth, shortest-path hue interpolation between palette colors.

CRITICAL: All hue interpolation uses circular shortest-path logic.
Never do naive linear lerp across hue — it causes ugly sweeps through
unintended color families (e.g. red→magenta going through the entire spectrum).
"""

import json
import math
import os
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# HSV color type
# ---------------------------------------------------------------------------

@dataclass
class HSVColor:
    h: float  # 0–360
    s: float  # 0.0–1.0
    v: float  # 0.0–1.0
    name: str = ""

    def to_tuple(self) -> Tuple[float, float, float]:
        return (self.h, self.s, self.v)


# ---------------------------------------------------------------------------
# Hue interpolation — shortest circular path
# ---------------------------------------------------------------------------

def lerp_hue_shortest(h1: float, h2: float, t: float) -> float:
    """
    Interpolate hue along the shortest arc of the color wheel.

    Example: red (0°) → magenta (300°) takes the short path through
    360°/0° rather than sweeping through orange/yellow/green/cyan/blue.
    """
    delta = (h2 - h1 + 540) % 360 - 180
    return (h1 + delta * t) % 360


def lerp_color(c1: HSVColor, c2: HSVColor, t: float) -> HSVColor:
    """Blend two HSV colors using shortest-path hue interpolation."""
    t = max(0.0, min(1.0, t))
    h = lerp_hue_shortest(c1.h, c2.h, t)
    s = c1.s + (c2.s - c1.s) * t
    v = c1.v + (c2.v - c1.v) * t
    return HSVColor(h=h, s=s, v=v)


# ---------------------------------------------------------------------------
# Palette definition
# ---------------------------------------------------------------------------

@dataclass
class Palette:
    name: str
    colors: List[HSVColor]
    transition_ms: float = 2000.0
    transition_type: str = "smooth"  # "smooth" | "snap"
    change_rule: str = "slow_blend"  # "slow_blend" | "energy_trigger" | "fast_beat" | "none"


def load_palette(json_path: str) -> Palette:
    """Load a Palette from a JSON file."""
    with open(json_path, "r") as f:
        data = json.load(f)

    colors = [
        HSVColor(
            h=c["h"],
            s=c["s"],
            v=c["v"],
            name=c.get("name", "")
        )
        for c in data["colors"]
    ]

    return Palette(
        name=data["name"],
        colors=colors,
        transition_ms=data.get("transition_ms", 2000.0),
        transition_type=data.get("transition_type", "smooth"),
        change_rule=data.get("change_rule", "slow_blend"),
    )


def load_all_palettes(palettes_dir: str) -> dict:
    """
    Load all .json palette files from a directory.
    Returns dict keyed by mode name (filename without extension).
    """
    palettes = {}
    if not os.path.isdir(palettes_dir):
        return palettes

    for fname in os.listdir(palettes_dir):
        if fname.endswith(".json"):
            key = fname.replace(".json", "")
            path = os.path.join(palettes_dir, fname)
            try:
                palettes[key] = load_palette(path)
            except Exception as e:
                print(f"[palettes] Failed to load {fname}: {e}")

    return palettes


# ---------------------------------------------------------------------------
# Palette blender — drives the slow color blend over time
# ---------------------------------------------------------------------------

class PaletteBlender:
    """
    Slowly cycles through the colors of the active palette.

    In Sprint 1, blending is time-driven (slow_blend mode) so you can see
    colors moving visually without needing phrase detection.

    Later, major transitions will be triggered by musical phrases.
    """

    def __init__(self, palette: Palette):
        self._palette    = palette
        self._color_idx  = 0           # current color index
        self._next_idx   = 1 % max(len(palette.colors), 1)
        self._blend_t    = 0.0         # 0.0 = at color_idx, 1.0 = at next_idx
        self._last_time  = time.monotonic()

    def set_palette(self, palette: Palette) -> None:
        """Switch to a new palette — restart blend from color index 0."""
        self._palette   = palette
        self._color_idx = 0
        self._next_idx  = 1 % max(len(palette.colors), 1)
        self._blend_t   = 0.0
        self._last_time = time.monotonic()

    def update(self, energy: float = 0.5) -> HSVColor:
        """
        Advance the blend and return the current blended color.

        energy — room energy 0.0–1.0 (currently not used to speed up blends,
                 but wired in for future phrase/energy-triggered changes).
        """
        now = time.monotonic()
        dt_ms = (now - self._last_time) * 1000.0
        self._last_time = now

        colors = self._palette.colors
        if not colors:
            return HSVColor(h=0, s=0, v=0)

        if len(colors) == 1:
            c = colors[0]
            return HSVColor(h=c.h, s=c.s, v=c.v)

        t_ms = self._palette.transition_ms
        if t_ms <= 0:
            t_ms = 2000.0

        # Advance blend fraction
        self._blend_t += dt_ms / t_ms

        if self._blend_t >= 1.0:
            # Advance to next color pair
            self._blend_t -= 1.0
            self._color_idx = self._next_idx
            self._next_idx  = (self._color_idx + 1) % len(colors)

        c1 = colors[self._color_idx]
        c2 = colors[self._next_idx]
        return lerp_color(c1, c2, self._blend_t)

    @property
    def palette_name(self) -> str:
        return self._palette.name
