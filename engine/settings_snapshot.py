"""
SettingsSnapshot — captures all parameters needed to reproduce a deterministic
lighting preview from a given AnalysisTimeline.

Stored inside LightingProgram (Sprint 4) so the exact settings used to generate
a lighting pass are always available alongside the generated output.
"""

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SettingsSnapshot:
    """
    Everything needed to reproduce a deterministic DeterministicEngine run.

    All optional overrides default to None, meaning "use the mode's default."
    The lighting engine respects these overrides when they are set.
    """
    # Required
    mode_key:    str
    palette_key: str

    # Global scaling
    master_dimmer: float = 1.0

    # Per-mode overrides (None = use Mode's built-in value)
    intensity_override:        Optional[float] = None
    base_brightness_override:  Optional[float] = None
    max_brightness_override:   Optional[float] = None
    pulse_amount_override:     Optional[float] = None
    saturation_scale_override: Optional[float] = None

    # Rig configuration at generation time
    fixture_rig_config: dict = field(default_factory=dict)

    # Timestamp
    created_at: float = field(default_factory=time.time)
