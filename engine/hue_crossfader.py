"""
HueCrossfader — smooths hue output across mode switches.

When a mode switch occurs the palette blender resets to the new palette's
first color, causing an instant hue jump. HueCrossfader captures the
departing hue and blends to the new hue over a configurable duration,
giving smooth visual transitions without touching the DMX output chain.
"""

import time
from typing import Optional

from engine.palettes import lerp_hue_shortest


class HueCrossfader:
    """
    Smooth hue transitions across mode switches.

    Usage:
        cf = HueCrossfader(duration_s=0.5)

        # On each mode switch (before palette reset):
        cf.snap(current_hue, instant=new_mode.transition_snap)

        # Every render frame:
        display_hue = cf.blend(raw_hue)
    """

    def __init__(self, duration_s: float = 0.5):
        self._duration = max(duration_s, 1e-6)
        self._prev_hue = 0.0
        self._start_t  = 0.0
        self._active   = False

    def snap(self, hue: float, now: Optional[float] = None,
             instant: bool = False) -> None:
        """Capture departing hue as the blend start point.

        instant=True (e.g. banger's transition_snap) skips the crossfade.
        """
        if instant:
            self._active = False
            return
        if now is None:
            now = time.monotonic()
        self._prev_hue = hue
        self._start_t  = now
        self._active   = True

    def blend(self, current_hue: float,
              now: Optional[float] = None) -> float:
        """Return the smoothed display hue for this frame."""
        if not self._active:
            return current_hue
        if now is None:
            now = time.monotonic()
        t = min(1.0, (now - self._start_t) / self._duration)
        if t >= 1.0:
            self._active = False
            return current_hue
        return lerp_hue_shortest(self._prev_hue, current_hue, t)

    def reset(self) -> None:
        """Clear crossfade state (used for deterministic replay)."""
        self._active = False
