"""
Safety rules for LightBrain.

These rules are applied after all lane/palette calculations and before final
DMX rendering. Safety always wins — blackout overrides everything.

Rules for Sprint 1:
- Blackout mode forces all outputs to 0 regardless of state
- Speech mode disables strobe
- Dinner mode disables strobe
- Strobe is disabled globally in Sprint 1 (placeholder for later)
- Master dimmer always scales the final output
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class SafetyState:
    blackout_active: bool = False
    strobe_allowed: bool = False    # Globally off in Sprint 1
    movement_allowed: bool = True   # Placeholder — moving heads Sprint 2+
    master_dimmer: float = 1.0      # 0.0–1.0 global scale
    mode_intensity_scale: float = 1.0


class SafetyEngine:
    """
    Applies safety constraints to computed output values.

    Call apply() after all lighting calculations to get final safe values.
    """

    def __init__(self):
        self.state = SafetyState()
        self._blackout_latch: bool = False  # tracks Space toggle

    def update_from_mode(self, mode) -> None:
        """Refresh safety state based on the active mode."""
        if mode.key == "blackout":
            self.state.blackout_active = True
        else:
            self.state.blackout_active = self._blackout_latch
            # Strobe stays globally off in Sprint 1
            self.state.strobe_allowed = False  # mode.allow_strobe when ready
            self.state.movement_allowed = mode.allow_movement
            self.state.mode_intensity_scale = mode.intensity_scale

    def toggle_blackout(self) -> bool:
        """Toggle the manual blackout latch. Returns new state."""
        self._blackout_latch = not self._blackout_latch
        self.state.blackout_active = self._blackout_latch
        return self._blackout_latch

    def set_master_dimmer(self, value: float) -> None:
        self.state.master_dimmer = max(0.0, min(1.0, value))

    def apply(self, brightness: float, strobe: float = 0.0) -> tuple:
        """
        Apply safety rules to brightness and strobe values.

        Returns (safe_brightness, safe_strobe) as 0.0–1.0 floats.
        Blackout forces both to 0.
        """
        if self.state.blackout_active:
            return 0.0, 0.0

        safe_brightness = (
            brightness
            * self.state.master_dimmer
            * self.state.mode_intensity_scale
        )
        safe_brightness = max(0.0, min(1.0, safe_brightness))

        safe_strobe = 0.0  # always 0 in Sprint 1
        return safe_brightness, safe_strobe
