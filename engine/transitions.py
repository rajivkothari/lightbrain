"""
Mode transition blender — smooth crossfade on mode switches.

Sprint 2: when the DJ switches modes, White/Amber/UV channel levels crossfade
from the previous levels to the new mode's levels over transition_ms.
Modes with transition_snap=True (e.g. Banger) switch instantly with no blend.

Callers snapshot their WAU values at switch time, then each frame call
update() to get blend_t (0.0 → 1.0) and lerp their own values.
"""

import time


class ModeTransitioner:
    """
    Provides a 0→1 blend factor during mode crossfades.

    Usage:
        transitioner = ModeTransitioner(initial_mode)

        # On mode switch:
        transitioner.switch(new_mode)

        # Each frame:
        blend_t = transitioner.update()   # 0.0 = all prev, 1.0 = all current

        # Caller lerps their values:
        eff_amber = prev_amber + (new_amber - prev_amber) * blend_t
    """

    DEFAULT_TRANSITION_MS = 800.0   # crossfade duration for normal modes

    def __init__(self, initial_mode):
        self._current   = initial_mode
        self._prev      = initial_mode
        self._blend_t   = 1.0
        self._trans_ms  = self.DEFAULT_TRANSITION_MS
        self._last_time = time.monotonic()

    def switch(self, new_mode) -> None:
        """
        Trigger a transition to new_mode.
        If new_mode.transition_snap is True the switch is instant (blend_t = 1.0).
        """
        if new_mode is self._current:
            return
        if getattr(new_mode, "transition_snap", False):
            self._prev    = new_mode
            self._current = new_mode
            self._blend_t = 1.0
        else:
            self._prev    = self._current
            self._current = new_mode
            self._blend_t = 0.0
        self._last_time = time.monotonic()

    def update(self) -> float:
        """
        Advance the crossfade timer; return blend_t in [0.0, 1.0].
        1.0 means transition complete; callers may skip blending when 1.0.
        """
        now   = time.monotonic()
        dt_ms = min((now - self._last_time) * 1000.0, 100.0)
        self._last_time = now
        if self._blend_t < 1.0:
            self._blend_t = min(1.0, self._blend_t + dt_ms / self._trans_ms)
        return self._blend_t

    @property
    def current_mode(self):
        return self._current

    @property
    def prev_mode(self):
        return self._prev

    @property
    def blend_t(self) -> float:
        return self._blend_t

    @staticmethod
    def lerp(a: float, b: float, t: float) -> float:
        return a + (b - a) * t
