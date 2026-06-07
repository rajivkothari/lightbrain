"""
Fixture State Model — high-level representation of what each fixture or
fixture group is doing at a given moment.

This is the contract between the lighting engine and the visualizer.
The DMX renderer can also consume these structs to generate DMX output,
keeping the rendering pipeline fixture-agnostic.
"""

from dataclasses import dataclass, field
from typing import List, Tuple


# RGB color type alias for clarity
RGB = Tuple[int, int, int]


@dataclass
class UplightState:
    """State for a single RGBWAUV uplight fixture."""
    fixture_id: str
    x: float
    y: float
    color_rgb: RGB
    brightness: float      # 0.0–1.0
    active: bool = True


@dataclass
class WashState:
    """State for a wide-beam wash fixture."""
    fixture_id: str
    x: float
    y: float
    color_rgb: RGB
    brightness: float      # 0.0–1.0
    radius: float          # nominal wash radius in pixels
    pulse_strength: float  # 0.0–1.0 — expands radius on bass hits
    active: bool = True


@dataclass
class BeamState:
    """State for a narrow-beam moving-head fixture."""
    fixture_id: str
    x: float
    y: float
    color_rgb: RGB
    brightness: float      # 0.0–1.0
    angle_degrees: float   # degrees from vertical (0=up, +right, -left)
    length: float          # beam length in pixels
    spread: float          # cone half-width in degrees
    movement_speed: float  # 0.0–1.0 — how fast the beam sweeps
    active: bool = True


@dataclass
class SparkleState:
    """State for a sparkle/derby effect (e.g. GigBAR ILS derby head)."""
    fixture_id: str
    x: float
    y: float
    color_rgb: RGB
    brightness: float      # 0.0–1.0
    sparkle_amount: float  # 0.0–1.0 — density of sparkle dots
    active: bool = True


@dataclass
class ImpactState:
    """State for a strobe/impact flash fixture or channel."""
    fixture_id: str
    x: float
    y: float
    brightness: float      # 0.0–1.0
    flash_active: bool     # True when a flash pulse is currently firing
    active: bool = True


@dataclass
class RigVisualState:
    """
    Complete visual state of the rig at one moment.

    The visualizer renders from this struct; the lighting engine populates it.
    """
    mode: str
    palette_name: str
    low_energy: float
    mid_energy: float
    high_energy: float
    overall_energy: float
    room_brightness: float
    impact_value: float
    uplights:  List[UplightState]
    washes:    List[WashState]
    beams:     List[BeamState]
    sparkles:  List[SparkleState]
    impacts:   List[ImpactState]
    blackout_active: bool
