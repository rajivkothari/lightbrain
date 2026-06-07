"""
DJFLX-style moving beam fixture mapper — 10-channel mode.

Channel layout (provisional, to be verified against physical unit):
  Ch 1: Pan          0=left, 128=centre, 255=right  (±90° range)
  Ch 2: Pan Fine     0-255 sub-degree fine adjust
  Ch 3: Tilt         0=back, 128=horizontal, 255=front
  Ch 4: Tilt Fine
  Ch 5: Red          0-255
  Ch 6: Green        0-255
  Ch 7: Blue         0-255
  Ch 8: Dimmer       0-255 (gamma-corrected)
  Ch 9: Speed        0=fastest movement, 255=slowest
  Ch10: Reset        255 = fixture reset (only sent explicitly)

The mapper reads `BeamState` from the scene layout and converts it to DMX.

BeamState → DMX mapping:
  angle_degrees   → Pan channel (0 = centre, ±90° range → 0–255)
  brightness      → Dimmer (gamma 2.2)
  color_rgb       → R / G / B channels directly
  movement_speed  → Speed (inverted: high speed → low DMX value)
  active=False    → dimmer zeroed immediately (no fade)
"""

from typing import Optional

from app.render.fixture_state import BeamState
from dmx.universe import DMXUniverse
from engine.gamma import apply_gamma_to_dmx
from fixtures.fixture import FixtureBase


# Channel offsets (0-indexed from start_address)
CH_PAN       = 0
CH_PAN_FINE  = 1
CH_TILT      = 2
CH_TILT_FINE = 3
CH_RED       = 4
CH_GREEN     = 5
CH_BLUE      = 6
CH_DIMMER    = 7
CH_SPEED     = 8
CH_RESET     = 9

NUM_CHANNELS = 10

# Show tilt: fixture aims slightly down-front toward the dance floor (0–255)
# 0=back-vertical, 128=horizontal, 255=front-down
# ~110 ≈ 55° down from vertical (typical stage position)
DEFAULT_TILT = 110

# Pan range: ±90° → 0-255 (centre=128)
PAN_RANGE_DEG = 90.0


class DJFLXBeam(FixtureBase):
    """
    10-channel moving beam fixture mapper.

    Converts BeamState (from SceneLayout) to DMX channel values.
    """

    def render_to_universe(
        self,
        universe:  DMXUniverse,
        state:     BeamState,
        gamma:     float = 2.2,
        tilt_dmx:  int   = DEFAULT_TILT,
    ) -> None:
        """
        Write this fixture's channels to universe based on BeamState.

        universe  — target DMX universe
        state     — current BeamState from scene layout
        gamma     — gamma exponent for the dimmer channel
        tilt_dmx  — fixed tilt position (0-255); default ≈ 55° down-front
        """
        base = self.dmx_address - 1   # convert 1-indexed to 0-indexed offset

        if not state.active:
            universe.set_channel(base + CH_DIMMER + 1, 0)
            return

        # Pan: angle_degrees 0=centre → 128; ±90° → 0 / 255
        pan_norm  = (state.angle_degrees / PAN_RANGE_DEG) * 0.5 + 0.5
        pan_norm  = max(0.0, min(1.0, pan_norm))
        pan_dmx   = int(pan_norm * 255)

        # Dimmer
        dimmer_dmx = apply_gamma_to_dmx(max(0.0, min(1.0, state.brightness)), gamma)

        # RGB — direct, no gamma on colour channels (colour mixing, not intensity)
        r, g, b = state.color_rgb
        r = max(0, min(255, int(r)))
        g = max(0, min(255, int(g)))
        b = max(0, min(255, int(b)))

        # Speed: movement_speed 0-1 → DMX 255-0 (inverted: 0=fastest)
        speed_dmx = int((1.0 - max(0.0, min(1.0, state.movement_speed))) * 255)

        universe.set_channels(base + 1, [
            pan_dmx,          # Ch1  Pan
            0,                # Ch2  Pan Fine (coarse only for now)
            tilt_dmx,         # Ch3  Tilt
            0,                # Ch4  Tilt Fine
            r,                # Ch5  Red
            g,                # Ch6  Green
            b,                # Ch7  Blue
            dimmer_dmx,       # Ch8  Dimmer
            speed_dmx,        # Ch9  Speed
            0,                # Ch10 Reset (never sent automatically)
        ])

    # ------------------------------------------------------------------
    # FixtureBase interface
    # ------------------------------------------------------------------

    @property
    def num_channels(self) -> int:
        return NUM_CHANNELS


def pan_degrees_to_dmx(angle_deg: float, pan_range: float = PAN_RANGE_DEG) -> int:
    """
    Convert a pan angle (degrees, 0=centre) to a DMX value 0-255.

    angle_deg  — degrees from centre; positive = right, negative = left
    pan_range  — half-range in degrees (default ±90°)
    """
    norm = (angle_deg / pan_range) * 0.5 + 0.5
    return int(max(0.0, min(1.0, norm)) * 255)


def dmx_to_pan_degrees(dmx: int, pan_range: float = PAN_RANGE_DEG) -> float:
    """Inverse of pan_degrees_to_dmx — for testing."""
    norm = dmx / 255.0
    return (norm - 0.5) * 2.0 * pan_range
