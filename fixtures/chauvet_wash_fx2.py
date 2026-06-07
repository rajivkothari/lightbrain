"""
Chauvet DJ Wash FX 2 — 8-channel DMX mapper.

Set the fixture's DMX personality to 8Ch.

8Ch layout:
  Ch 1  Red       0–255
  Ch 2  Green     0–255
  Ch 3  Blue      0–255
  Ch 4  UV        0–255
  Ch 5  Strobe    0=off, 1–255=0–20 Hz slow to fast
  Ch 6  Auto Program   0–255  (0 = off / manual colour control)
  Ch 7  Auto Program Speed    (0 = unused in manual mode)
  Ch 8  Dimmer    0–255  (only active during auto/sound programs)

In manual colour mode (Ch6=0) there is no separate master dimmer channel —
brightness is baked into the RGB/UV values directly.

The fixture has no White or Amber channel; those parameters are silently
ignored.
"""

import colorsys
import warnings

from dmx.universe import DMXUniverse
from engine.gamma import apply_gamma_to_dmx
from fixtures.fixture import FixtureBase


# Channel offsets (0-indexed from dmx_address)
CH_RED    = 0
CH_GREEN  = 1
CH_BLUE   = 2
CH_UV     = 3
CH_STROBE = 4
CH_PROG   = 5   # Auto Program — kept at 0 for manual colour
CH_SPEED  = 6   # Auto Program Speed — unused in manual mode
CH_DIMMER = 7   # Dimmer — only active during auto/sound programs

NUM_CHANNELS = 8

# Strobe: 0=off, 1=slowest (~0.5 Hz), 255=fastest (~20 Hz)
_STROBE_MIN = 1
_STROBE_MAX = 255


class ChauvetWashFX2(FixtureBase):
    """
    Chauvet DJ Wash FX 2 in 8-channel mode.

    All 6 RGBU zones receive the same colour — use the 28Ch personality if
    per-zone chase effects are required.

    Caller interface is the same as RockWedge:
        render_to_universe(universe, brightness, hue, saturation, value,
                           strobe, white, amber, uv)
    The ``white`` and ``amber`` parameters are accepted but not used (the
    fixture has no W/A channels).
    """

    def render_to_universe(
        self,
        universe:   DMXUniverse,
        brightness: float = 1.0,   # 0–1 master dimmer
        hue:        float = 0.0,   # 0–360
        saturation: float = 1.0,   # 0–1
        value:      float = 1.0,   # 0–1 lane value
        strobe:     float = 0.0,   # 0–1  (0 = off)
        white:      float = 0.0,   # ignored (no W channel)
        amber:      float = 0.0,   # ignored (no A channel)
        uv:         float = 0.0,   # 0–1
        gamma:      float = 2.2,
    ) -> None:
        addr = self.dmx_address
        if addr + NUM_CHANNELS - 1 > 512:
            warnings.warn(
                f"ChauvetWashFX2 '{self.name}' at address {addr}: "
                f"channels {addr}–{addr + NUM_CHANNELS - 1} exceed 512",
                RuntimeWarning, stacklevel=2,
            )

        # Bake brightness into colour (no separate dimmer in manual mode)
        final_v = max(0.0, min(1.0, value * brightness))
        r_f, g_f, b_f = colorsys.hsv_to_rgb(hue / 360.0, saturation, final_v)

        r_dmx  = apply_gamma_to_dmx(r_f, gamma)
        g_dmx  = apply_gamma_to_dmx(g_f, gamma)
        b_dmx  = apply_gamma_to_dmx(b_f, gamma)
        uv_dmx = apply_gamma_to_dmx(max(0.0, min(1.0, uv * brightness)), gamma)

        # Strobe: 0 = off, 1–255 = 0–20 Hz
        if strobe > 0.0:
            strobe_dmx = _STROBE_MIN + int(
                max(0.0, min(1.0, strobe)) * (_STROBE_MAX - _STROBE_MIN)
            )
        else:
            strobe_dmx = 0

        universe.set_channels(addr, [
            r_dmx,      # Ch1 Red
            g_dmx,      # Ch2 Green
            b_dmx,      # Ch3 Blue
            uv_dmx,     # Ch4 UV
            strobe_dmx, # Ch5 Strobe
            0,          # Ch6 Auto Program (0 = manual colour)
            0,          # Ch7 Auto Program Speed (unused)
            0,          # Ch8 Dimmer (auto programs only)
        ])

    def get_channel_labels(self) -> dict:
        a = self.dmx_address
        return {
            "Red":    a + CH_RED,
            "Green":  a + CH_GREEN,
            "Blue":   a + CH_BLUE,
            "UV":     a + CH_UV,
            "Strobe": a + CH_STROBE,
        }
