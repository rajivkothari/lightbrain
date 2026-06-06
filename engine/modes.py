"""
Mode definitions for LightBrain.

Each mode corresponds to a DJ event moment and selects the appropriate
palette + behavior constraints. Safety rules are applied on top in safety.py.
"""

from dataclasses import dataclass


@dataclass
class Mode:
    key: str           # internal key, matches palette JSON filename
    display_name: str  # shown in UI
    palette_key: str   # which palette to load
    allow_strobe: bool = False
    allow_movement: bool = True  # placeholder — moving heads Sprint 2+
    intensity_scale: float = 1.0  # master intensity multiplier for this mode


# All supported modes
MODES: dict = {
    "dinner": Mode(
        key="dinner",
        display_name="Dinner",
        palette_key="dinner",
        allow_strobe=False,
        intensity_scale=0.7,
    ),
    "speech": Mode(
        key="speech",
        display_name="Speech",
        palette_key="speech",
        allow_strobe=False,
        allow_movement=False,
        intensity_scale=0.8,
    ),
    "open_dance": Mode(
        key="open_dance",
        display_name="Open Dance",
        palette_key="open_dance",
        allow_strobe=False,   # strobe stays off in Sprint 1
        intensity_scale=1.0,
    ),
    "banger": Mode(
        key="banger",
        display_name="Banger",
        palette_key="banger",
        allow_strobe=False,   # strobe stays off in Sprint 1
        intensity_scale=1.0,
    ),
    "indian_latin": Mode(
        key="indian_latin",
        display_name="Indian / Latin",
        palette_key="indian_latin",
        allow_strobe=False,
        intensity_scale=1.0,
    ),
    "slow_dance": Mode(
        key="slow_dance",
        display_name="Slow Dance",
        palette_key="slow_dance",
        allow_strobe=False,
        intensity_scale=0.75,
    ),
    "blackout": Mode(
        key="blackout",
        display_name="BLACKOUT",
        palette_key="open_dance",  # palette doesn't matter in blackout
        allow_strobe=False,
        intensity_scale=0.0,
    ),
}

# Keyboard shortcut → mode key mapping
# (used by terminal input handler if implemented)
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
