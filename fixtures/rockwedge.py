"""
RockWedge-style RGBWAUV uplight fixture mapper.

Provisional 8-channel mode (to be verified against physical unit):
  Ch1: Dimmer
  Ch2: Red
  Ch3: Green
  Ch4: Blue
  Ch5: White
  Ch6: Amber
  Ch7: UV
  Ch8: Strobe

HSV input is converted to RGB for the color channels.
Gamma correction is applied to dimmer and color channels separately.
White, Amber, and UV channels are set to 0 in Sprint 1 — they will be
driven by palette rules in a later sprint.
"""

import colorsys
import warnings
from typing import Tuple

from dmx.universe import DMXUniverse
from engine.gamma import apply_gamma_to_dmx, apply_gamma
from fixtures.fixture import FixtureBase


# Channel offsets from start_address (0-indexed offsets)
CH_DIMMER = 0
CH_RED    = 1
CH_GREEN  = 2
CH_BLUE   = 3
CH_WHITE  = 4
CH_AMBER  = 5
CH_UV     = 6
CH_STROBE = 7

NUM_CHANNELS = 8


def hsv_to_rgb_normalized(h: float, s: float, v: float
                           ) -> Tuple[float, float, float]:
    """Convert HSV (h=0-360, s=0-1, v=0-1) to RGB (all 0.0-1.0)."""
    return colorsys.hsv_to_rgb(h / 360.0, s, v)


class RockWedge(FixtureBase):
    """
    RGBWAUV wash uplight in 8-channel mode.

    render_to_universe() maps the lighting engine output onto DMX channels.
    """

    @property
    def channel_count(self) -> int:
        return NUM_CHANNELS

    def render_to_universe(
        self,
        universe: DMXUniverse,
        brightness: float = 1.0,   # 0.0–1.0 final brightness
        hue: float = 0.0,          # 0–360
        saturation: float = 1.0,   # 0.0–1.0
        value: float = 1.0,        # 0.0–1.0 (color value, separate from brightness)
        strobe: float = 0.0,       # 0.0–1.0 (0 = off)
        white: float = 0.0,        # Sprint 2: Ch5 White  0.0–1.0
        amber: float = 0.0,        # Sprint 2: Ch6 Amber  0.0–1.0
        uv: float = 0.0,           # Sprint 2: Ch7 UV     0.0–1.0
        gamma: float = 2.2,
    ) -> None:
        """
        Write all 8 channels to the DMX universe.

        brightness — overall dimmer (combined with HSV value)
        hue        — color hue 0–360
        saturation — color saturation 0.0–1.0
        value      — color value from lane output (brightness baked in)
        strobe     — strobe intensity (always 0 in Sprint 1)
        white      — white channel level 0.0–1.0 (palette-driven Sprint 2)
        amber      — amber channel level 0.0–1.0 (palette-driven Sprint 2)
        uv         — UV channel level 0.0–1.0 (palette-driven Sprint 2)
        """
        addr = self.dmx_address
        if addr + NUM_CHANNELS - 1 > 512:
            warnings.warn(
                f"RockWedge '{self.name}' at address {addr}: "
                f"channels {addr}–{addr + NUM_CHANNELS - 1} exceed DMX universe limit 512",
                RuntimeWarning, stacklevel=2,
            )

        # Final brightness = lane value (safety-scaled) × any additional scale
        final_brightness = max(0.0, min(1.0, value * brightness))

        # Ch1 Dimmer: carries the overall brightness via gamma.
        # On a real fixture the dimmer gates all color channels, so
        # we set it to the brightness level and RGB to pure hue.
        dimmer_dmx = apply_gamma_to_dmx(final_brightness, gamma)

        # Convert HSV → RGB: pure color at full value (v=1.0).
        # Brightness is expressed through the Dimmer channel, NOT baked into
        # the color channels — avoids double-dimming when hardware multiplies
        # Ch1 × Ch2-4.
        r_norm, g_norm, b_norm = hsv_to_rgb_normalized(hue, saturation, 1.0)

        r_dmx = apply_gamma_to_dmx(r_norm, gamma)
        g_dmx = apply_gamma_to_dmx(g_norm, gamma)
        b_dmx = apply_gamma_to_dmx(b_norm, gamma)

        # Sprint 2: White/Amber/UV channels driven by palette rules
        white_dmx = apply_gamma_to_dmx(max(0.0, min(1.0, white)), gamma)
        amber_dmx = apply_gamma_to_dmx(max(0.0, min(1.0, amber)), gamma)
        uv_dmx    = apply_gamma_to_dmx(max(0.0, min(1.0, uv)),    gamma)

        # Strobe: Ch8 range 0=off, 11=slowest, 255=fastest (per RockWedge datasheet)
        strobe_dmx = (11 + int(max(0.0, min(1.0, strobe)) * 244)) if strobe > 0.0 else 0

        universe.set_channels(addr, [
            dimmer_dmx,   # Ch1 Dimmer
            r_dmx,        # Ch2 Red
            g_dmx,        # Ch3 Green
            b_dmx,        # Ch4 Blue
            white_dmx,    # Ch5 White
            amber_dmx,    # Ch6 Amber
            uv_dmx,       # Ch7 UV
            strobe_dmx,   # Ch8 Strobe — disabled Sprint 1
        ])

    def get_channel_labels(self) -> dict:
        """Return a label → channel_number mapping for the overlay."""
        a = self.dmx_address
        return {
            "Dimmer": a + CH_DIMMER,
            "Red":    a + CH_RED,
            "Green":  a + CH_GREEN,
            "Blue":   a + CH_BLUE,
            "White":  a + CH_WHITE,
            "Amber":  a + CH_AMBER,
            "UV":     a + CH_UV,
            "Strobe": a + CH_STROBE,
        }
