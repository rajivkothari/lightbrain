"""
Mode definitions for LightBrain.

Each mode corresponds to a DJ event moment and selects the appropriate
palette + behavior constraints. Safety rules are applied on top in safety.py.

Sprint 1B adds per-mode brightness profiles so each mode has its own
floor/ceiling, bass-breathing depth, saturation character, and palette
hold time.
"""

from dataclasses import dataclass, field


@dataclass
class Mode:
    key: str           # internal key, matches palette JSON filename
    display_name: str  # shown in UI
    palette_key: str   # which palette to load
    allow_strobe: bool = False
    allow_movement: bool = True  # placeholder — moving heads Sprint 2+
    intensity_scale: float = 1.0  # master intensity multiplier for this mode
    # --- Sprint 1B: brightness profile ---
    base_brightness: float = 0.2   # floor brightness when room energy is 0
    max_brightness: float = 1.0    # ceiling brightness when room energy is 1.0
    pulse_amount: float = 0.15     # bass-breathing depth (impact multiplier)
    saturation_scale: float = 1.0  # multiplies palette color saturation
    hold_ms: float = 0.0           # how long to hold each palette color (ms)


# All supported modes
MODES: dict = {
    "dinner": Mode(
        key="dinner",
        display_name="Dinner",
        palette_key="dinner",
        allow_strobe=False,
        intensity_scale=0.7,
        base_brightness=0.35,
        max_brightness=0.65,
        pulse_amount=0.06,
        saturation_scale=0.85,
        hold_ms=8000.0,
    ),
    "speech": Mode(
        key="speech",
        display_name="Speech",
        palette_key="speech",
        allow_strobe=False,
        allow_movement=False,
        intensity_scale=0.8,
        base_brightness=0.5,
        max_brightness=0.70,
        pulse_amount=0.02,
        saturation_scale=0.5,
        hold_ms=15000.0,
    ),
    "open_dance": Mode(
        key="open_dance",
        display_name="Open Dance",
        palette_key="open_dance",
        allow_strobe=False,
        intensity_scale=1.0,
        base_brightness=0.2,
        max_brightness=1.0,
        pulse_amount=0.15,
        saturation_scale=1.0,
        hold_ms=4000.0,
    ),
    "banger": Mode(
        key="banger",
        display_name="Banger",
        palette_key="banger",
        allow_strobe=False,
        intensity_scale=1.0,
        base_brightness=0.3,
        max_brightness=1.0,
        pulse_amount=0.20,
        saturation_scale=1.0,
        hold_ms=2000.0,
    ),
    "indian_latin": Mode(
        key="indian_latin",
        display_name="Indian / Latin",
        palette_key="indian_latin",
        allow_strobe=False,
        intensity_scale=1.0,
        base_brightness=0.25,
        max_brightness=1.0,
        pulse_amount=0.18,
        saturation_scale=1.0,
        hold_ms=3000.0,
    ),
    "slow_dance": Mode(
        key="slow_dance",
        display_name="Slow Dance",
        palette_key="slow_dance",
        allow_strobe=False,
        intensity_scale=0.75,
        base_brightness=0.40,
        max_brightness=0.75,
        pulse_amount=0.08,
        saturation_scale=0.75,
        hold_ms=10000.0,
    ),
    "blackout": Mode(
        key="blackout",
        display_name="BLACKOUT",
        palette_key="open_dance",
        allow_strobe=False,
        intensity_scale=0.0,
        base_brightness=0.0,
        max_brightness=0.0,
        pulse_amount=0.0,
        saturation_scale=1.0,
        hold_ms=0.0,
    ),
}

# Keyboard shortcut → mode key mapping
KEYBOARD_MAP: dict = {
    "o": "open_dance",
    "d": "dinner",
    "b": "banger",
    "i": "indian_latin",
    "s": "speech",
    "l": "slow_dance",
    " ": "blackout",   # Space = blackout toggle
    "q": "quit",
}


def get_mode(key: str) -> Mode:
    """Return the Mode for a given key, defaulting to open_dance."""
    return MODES.get(key, MODES["open_dance"])
