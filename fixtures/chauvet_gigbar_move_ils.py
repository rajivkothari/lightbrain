"""
Chauvet DJ GigBAR Move + ILS — 29-channel DMX mapper.

Set the fixture's DMX personality to 29Ch.

29Ch layout (one set of controls for all pars, one spot head):
  Ch  1   Par Red          0–255
  Ch  2   Par Green        0–255
  Ch  3   Par Blue         0–255
  Ch  4   Par Amber        0–255
  Ch  5   Par White        0–255
  Ch  6   Par UV           0–255
  Ch  7   Par Strobe       0–250=slow–fast, 251–255=sound-active
  Ch  8   Derby Red        0–255
  Ch  9   Derby Green      0–255
  Ch 10   Derby Blue       0–255
  Ch 11   Derby White      0–255
  Ch 12   Derby Strobe     0–250=slow–fast, 251–255=sound-active
  Ch 13   Derby Rotation   0=stop, 1–127=CW slow–fast, 128=stop, 129–255=CCW slow–fast
  Ch 14   Flash LED 1      0–255
  Ch 15   Flash LED 2      0–255
  Ch 16   Flash LED 3      0–255
  Ch 17   Flash LED 4      0–255
  Ch 18   Flash Strobe     0–250=slow–fast, 251–255=sound-active
  Ch 19   Laser control    0=blackout, 1–255=colors/patterns
  Ch 20   Laser patterns   0–255
  Ch 21   Spot Pan         0–255 (0=0°, 128≈270°, 255=540°)
  Ch 22   Spot Fine Pan    0–255
  Ch 23   Spot Tilt        0–255 (0=back-vertical, 255=front-down, ~90=floor-facing)
  Ch 24   Spot Fine Tilt   0–255
  Ch 25   Spot Pan/Tilt Speed   0–255 (0=fastest, 255=slowest)
  Ch 26   Spot Color Wheel 0–6=white, 7–13=red, 14–20=orange, 21–27=yellow,
                           28–34=green, 35–41=blue, 42–48=CTO, 49–55=cyan,
                           56–62=magenta, 63–64=lime, 65–189=color index,
                           190–255=scroll
  Ch 27   Spot Gobo Wheel  0–5=open, 6–117=gobos 1–9, …, 128–255=scroll
  Ch 28   Spot Dimmer      0–255
  Ch 29   Spot Strobe      0–3=closed, 4–7=open, 8–76=strobe slow–fast,
                           77–145=pulse, 146–215=random, 216–255=open

Sections
--------
Par wash   — audio-reactive colour matching the room lane (RGBAWUV).
Derby      — same hue as pars; rotates proportionally to room energy.
Flash LEDs — ambient white fill at a fraction of room brightness.
Laser      — OFF (0) by default; enable per-event by calling enable_laser().
Spot heads — fixed aim position (configurable via rig_config.json or
             set_spot_aim()); dimmer tracks room brightness; colour wheel open.
"""

import colorsys
import warnings
from dataclasses import dataclass, field
from typing import Optional

from dmx.universe import DMXUniverse
from engine.gamma import apply_gamma_to_dmx
from fixtures.fixture import FixtureBase


# ── Channel offsets (0-indexed from dmx_address) ──────────────────────────

# Par (Ch 1–7)
CH_PAR_RED    = 0
CH_PAR_GREEN  = 1
CH_PAR_BLUE   = 2
CH_PAR_AMBER  = 3
CH_PAR_WHITE  = 4
CH_PAR_UV     = 5
CH_PAR_STROBE = 6

# Derby (Ch 8–13)
CH_DERBY_RED   = 7
CH_DERBY_GREEN = 8
CH_DERBY_BLUE  = 9
CH_DERBY_WHITE = 10
CH_DERBY_STROBE = 11
CH_DERBY_ROTATION = 12

# Flash LEDs (Ch 14–18)
CH_FLASH_1     = 13
CH_FLASH_2     = 14
CH_FLASH_3     = 15
CH_FLASH_4     = 16
CH_FLASH_STROBE = 17

# Laser (Ch 19–20)
CH_LASER_COLOR   = 18
CH_LASER_PATTERN = 19

# Spot (Ch 21–29)
CH_SPOT_PAN        = 20
CH_SPOT_PAN_FINE   = 21
CH_SPOT_TILT       = 22
CH_SPOT_TILT_FINE  = 23
CH_SPOT_SPEED      = 24
CH_SPOT_COLOR      = 25
CH_SPOT_GOBO       = 26
CH_SPOT_DIMMER     = 27
CH_SPOT_STROBE     = 28

NUM_CHANNELS = 29

# ── Strobe constants ───────────────────────────────────────────────────────
# Par / Derby / Flash strobe: 0=off, 1=slowest, 250=fastest
_PAR_STROBE_MIN  = 1
_PAR_STROBE_MAX  = 250

# Spot strobe channel: 4–7=open (no strobe), 8–76=strobe slow–fast
_SPOT_STROBE_OPEN = 4        # open shutter, no strobe
_SPOT_STROBE_MIN  = 8
_SPOT_STROBE_MAX  = 76

# Derby rotation: 129–255=CCW slow–fast; 0=stop
_DERBY_ROTATE_CCW_BASE = 129  # slowest CCW

# Flash ambient fill level (fraction of par brightness)
_FLASH_FILL_RATIO = 0.15

# Default spot aim: pointing toward the dance floor
_DEFAULT_SPOT_PAN_DEG  = 270.0   # centre of 540° range
_DEFAULT_SPOT_TILT_DMX = 90      # ~64°  down from vertical, floor-facing
_SPOT_PAN_RANGE_DEG    = 270.0   # half of 540°; full range = 0–540°


def _par_strobe(strobe_0_1: float) -> int:
    if strobe_0_1 <= 0.0:
        return 0
    return _PAR_STROBE_MIN + int(
        max(0.0, min(1.0, strobe_0_1)) * (_PAR_STROBE_MAX - _PAR_STROBE_MIN)
    )


def _spot_strobe_dmx(strobe_0_1: float) -> int:
    if strobe_0_1 <= 0.0:
        return _SPOT_STROBE_OPEN
    return _SPOT_STROBE_MIN + int(
        max(0.0, min(1.0, strobe_0_1)) * (_SPOT_STROBE_MAX - _SPOT_STROBE_MIN)
    )


def _pan_deg_to_dmx(pan_deg: float) -> int:
    """540° range: 0°=0, 270°=128, 540°=255."""
    return int(max(0.0, min(1.0, pan_deg / 540.0)) * 255)


class ChauvetGigBarMoveILS(FixtureBase):
    """
    Chauvet DJ GigBAR Move + ILS in 29-channel mode.

    Call render_to_universe() once per frame with the room lane values;
    all sub-sections are driven from the same inputs:

        Par wash   → hue/saturation/value/brightness/white/amber/uv
        Derby      → same hue, CCW rotation speed scales with room energy
        Flash LEDs → ambient white fill at _FLASH_FILL_RATIO of brightness
        Laser      → off unless enable_laser() is called
        Spot heads → fixed aim (set via config or set_spot_aim())

    Use set_spot_aim(pan_deg, tilt_dmx) to reposition the moving head from
    the iPad aiming joystick.
    """

    def __init__(
        self,
        fixture_id:     str,
        name:           str,
        dmx_address:    int,
        lane:           str   = "room",
        group:          str   = "all",
        spot_pan_deg:   float = _DEFAULT_SPOT_PAN_DEG,
        spot_tilt_dmx:  int   = _DEFAULT_SPOT_TILT_DMX,
        laser_enabled:  bool  = False,
    ):
        super().__init__(
            fixture_id=fixture_id,
            name=name,
            dmx_address=dmx_address,
            lane=lane,
            group=group,
        )
        self._spot_pan_deg  = float(spot_pan_deg)
        self._spot_tilt_dmx = int(spot_tilt_dmx)
        self._laser_enabled = laser_enabled
        self._derby_enabled = True

    # ── Public controls ──────────────────────────────────────────────────

    def set_spot_aim(self, pan_deg: float, tilt_dmx: int) -> None:
        """Reposition the spot head (called by the iPad aiming joystick)."""
        self._spot_pan_deg  = float(pan_deg)
        self._spot_tilt_dmx = max(0, min(255, int(tilt_dmx)))

    def enable_laser(self, enabled: bool = True) -> None:
        self._laser_enabled = enabled

    def set_derby_enabled(self, enabled: bool) -> None:
        self._derby_enabled = bool(enabled)

    # ── Core render ──────────────────────────────────────────────────────

    def render_to_universe(
        self,
        universe:   DMXUniverse,
        brightness: float = 1.0,   # 0–1 master dimmer
        hue:        float = 0.0,   # 0–360
        saturation: float = 1.0,   # 0–1
        value:      float = 1.0,   # 0–1 room energy value
        strobe:     float = 0.0,   # 0–1 strobe rate
        white:      float = 0.0,   # 0–1 Par White channel
        amber:      float = 0.0,   # 0–1 Par Amber channel
        uv:         float = 0.0,   # 0–1 Par UV channel
        gamma:      float = 2.2,
    ) -> None:
        addr = self.dmx_address
        if addr + NUM_CHANNELS - 1 > 512:
            warnings.warn(
                f"ChauvetGigBarMoveILS '{self.name}' at address {addr}: "
                f"channels {addr}–{addr + NUM_CHANNELS - 1} exceed 512",
                RuntimeWarning, stacklevel=2,
            )

        final_v = max(0.0, min(1.0, value * brightness))
        r_f, g_f, b_f = colorsys.hsv_to_rgb(hue / 360.0, saturation, final_v)

        r_dmx = apply_gamma_to_dmx(r_f, gamma)
        g_dmx = apply_gamma_to_dmx(g_f, gamma)
        b_dmx = apply_gamma_to_dmx(b_f, gamma)
        w_dmx = apply_gamma_to_dmx(max(0.0, min(1.0, white * brightness)), gamma)
        a_dmx = apply_gamma_to_dmx(max(0.0, min(1.0, amber * brightness)), gamma)
        uv_dmx = apply_gamma_to_dmx(max(0.0, min(1.0, uv * brightness)), gamma)

        par_strobe_dmx = _par_strobe(strobe)

        # Derby: same hue as pars; CCW rotation proportional to room energy
        if self._derby_enabled:
            dr_dmx = apply_gamma_to_dmx(r_f, gamma)
            dg_dmx = apply_gamma_to_dmx(g_f, gamma)
            db_dmx = apply_gamma_to_dmx(b_f, gamma)
            dw_dmx = apply_gamma_to_dmx(max(0.0, min(1.0, white * brightness * 0.5)), gamma)
            derby_strobe_dmx = _par_strobe(strobe)
            if final_v > 0.05:
                rotation_speed = int(final_v * 126)
                derby_rot_dmx  = _DERBY_ROTATE_CCW_BASE + rotation_speed
            else:
                derby_rot_dmx = 0
        else:
            dr_dmx = dg_dmx = db_dmx = dw_dmx = 0
            derby_strobe_dmx = derby_rot_dmx = 0

        # Flash LEDs: low-level ambient white fill
        flash_level = apply_gamma_to_dmx(
            max(0.0, min(1.0, final_v * _FLASH_FILL_RATIO)), gamma
        )
        flash_strobe_dmx = _par_strobe(strobe)

        # Laser: off unless explicitly enabled
        laser_color_dmx   = 1 if self._laser_enabled else 0
        laser_pattern_dmx = 0

        # Spot: fixed aim, open white (colour wheel at 0), open gobo (0)
        spot_pan_dmx    = _pan_deg_to_dmx(self._spot_pan_deg)
        spot_tilt_dmx   = self._spot_tilt_dmx
        spot_speed_dmx  = 200   # slow tracking (0=fastest, 255=slowest)
        spot_dimmer_dmx = apply_gamma_to_dmx(final_v, gamma)
        spot_strobe_dmx = _spot_strobe_dmx(strobe)

        universe.set_channels(addr, [
            # Par (Ch 1–7)
            r_dmx,              # Ch  1 Par Red
            g_dmx,              # Ch  2 Par Green
            b_dmx,              # Ch  3 Par Blue
            a_dmx,              # Ch  4 Par Amber
            w_dmx,              # Ch  5 Par White
            uv_dmx,             # Ch  6 Par UV
            par_strobe_dmx,     # Ch  7 Par Strobe
            # Derby (Ch 8–13)
            dr_dmx,             # Ch  8 Derby Red
            dg_dmx,             # Ch  9 Derby Green
            db_dmx,             # Ch 10 Derby Blue
            dw_dmx,             # Ch 11 Derby White
            derby_strobe_dmx,   # Ch 12 Derby Strobe
            derby_rot_dmx,      # Ch 13 Derby Rotation (CCW, energy-scaled)
            # Flash LEDs (Ch 14–18)
            flash_level,        # Ch 14 Flash LED 1
            flash_level,        # Ch 15 Flash LED 2
            flash_level,        # Ch 16 Flash LED 3
            flash_level,        # Ch 17 Flash LED 4
            flash_strobe_dmx,   # Ch 18 Flash Strobe
            # Laser (Ch 19–20)
            laser_color_dmx,    # Ch 19 Laser color (0=blackout)
            laser_pattern_dmx,  # Ch 20 Laser pattern
            # Spot (Ch 21–29)
            spot_pan_dmx,       # Ch 21 Spot Pan
            0,                  # Ch 22 Spot Fine Pan
            spot_tilt_dmx,      # Ch 23 Spot Tilt
            0,                  # Ch 24 Spot Fine Tilt
            spot_speed_dmx,     # Ch 25 Spot Pan/Tilt Speed
            0,                  # Ch 26 Spot Color Wheel (0=white/open)
            0,                  # Ch 27 Spot Gobo (0=open)
            spot_dimmer_dmx,    # Ch 28 Spot Dimmer
            spot_strobe_dmx,    # Ch 29 Spot Strobe
        ])

    # ── Diagnostic helper ────────────────────────────────────────────────

    def get_channel_labels(self) -> dict:
        a = self.dmx_address
        return {
            "Par Red":        a + CH_PAR_RED,
            "Par Green":      a + CH_PAR_GREEN,
            "Par Blue":       a + CH_PAR_BLUE,
            "Par Amber":      a + CH_PAR_AMBER,
            "Par White":      a + CH_PAR_WHITE,
            "Par UV":         a + CH_PAR_UV,
            "Par Strobe":     a + CH_PAR_STROBE,
            "Derby Rot":      a + CH_DERBY_ROTATION,
            "Flash 1":        a + CH_FLASH_1,
            "Laser":          a + CH_LASER_COLOR,
            "Spot Pan":       a + CH_SPOT_PAN,
            "Spot Tilt":      a + CH_SPOT_TILT,
            "Spot Dimmer":    a + CH_SPOT_DIMMER,
            "Spot Strobe":    a + CH_SPOT_STROBE,
        }
